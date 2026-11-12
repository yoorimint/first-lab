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
                   mock.patch.object(api_writer, "CLAUDE_BIN", str(self.tmp / "claude"))]
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
        self.assertIn("FIRSTLAB_LOCAL=1", api_writer.PROMPT)
        page = (ROOT / "wordpress" / "mu-plugins" / "first-lab-bot-settings.php").read_text(encoding="utf-8")
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
