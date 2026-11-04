"""배포 전 자동 검사 (GitHub Actions). 서버·API 없이 도는 순수 로직만 확인한다.
실행: python -m unittest discover -s tests -v
"""
import json
import sqlite3
import sys
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import competition, factcheck, keyword_db, refresh, search_console, state  # noqa: E402
from bot.discover import _combos  # noqa: E402
from bot.fix_posts import patch_content  # noqa: E402
from bot.keyword_filter import is_allowed, is_brand, subject  # noqa: E402
from bot.scoring import score  # noqa: E402
from bot.select_keyword import ranked_candidates  # noqa: E402
from bot.settings import BotSettings  # noqa: E402


def memory_db(rows):
    """(키워드, 검색량, 상태, 경쟁도) 목록으로 메모리 DB 생성."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(keyword_db.SCHEMA)
    keyword_db._migrate(conn)
    conn.executescript(search_console.SCHEMA)
    for kw, total, status, difficulty in rows:
        conn.execute(
            "INSERT INTO keywords (keyword, norm, total, status, first_seen, last_checked, difficulty)"
            " VALUES (?,?,?,?,?,?,?)",
            (kw, keyword_db.norm(kw), total, status, "2026-11-04", "2026-11-04", difficulty))
    return conn


class StateTest(unittest.TestCase):
    def test_php_empty_dict_becomes_dict(self):
        raw = json.dumps({"plan": [], "done": [], "refresh": [], "spend": [], "log": []})
        with mock.patch.object(state, "wp_cli", return_value=raw):
            s = state.load_state()
        for key in ("plan", "done", "refresh", "spend"):
            self.assertEqual(s[key], {})

    def test_spend_accumulates_today_only(self):
        s = {"spend": {"2000-01-01": 9.0}}
        state.add_spend(s, 0.5)
        state.add_spend(s, 0.25)
        self.assertEqual(len(s["spend"]), 1)
        self.assertAlmostEqual(state.spent_today(s), 0.75)


class KeywordRuleTest(unittest.TestCase):
    def test_blocked_words(self):
        self.assertFalse(is_allowed("알뜰폰요금제추천"))
        self.assertFalse(is_allowed("자동차보험조회이벤트"))
        self.assertTrue(is_allowed("하이패스카드신청"))

    def test_brand(self):
        self.assertTrue(is_brand("KT알뜰폰요금제"))
        self.assertTrue(is_brand("귀뚜라미보일러가격"))
        self.assertFalse(is_brand("자동차정기검사비용"))
        self.assertFalse(is_brand("국민연금수령나이"))

    def test_brand_penalty_and_no_ad_bonus(self):
        plain = score("자동차정기검사비용", "자동차정기검사비용", 4000, "높음", 100, None, "자동차")
        brand = score("금호타이어가격표", "금호타이어가격표", 4000, "높음", 100, None, "타이어")
        self.assertLess(brand, plain)
        self.assertEqual(score("가나다라마바사", "가나다라마바사", 4000, "높음", 0, None, None),
                         score("가나다라마바사", "가나다라마바사", 4000, "낮음", 0, None, None))

    def test_subject(self):
        self.assertEqual(subject("자동차정기검사비용"), "자동차정기검사")
        self.assertEqual(subject("자동차정기검사조회"), "자동차정기검사")
        self.assertEqual(subject("2027근로장려금"), "근로장려금")
        self.assertNotEqual(subject("자동차종합검사비용"), subject("자동차정기검사비용"))

    def test_combos_include_years(self):
        combos = _combos("근로장려금")
        self.assertGreater(len(combos), 50)
        self.assertTrue(any(c[:4].isdigit() for c in combos))
        self.assertEqual(len(combos), len(set(combos)))


class SelectionTest(unittest.TestCase):
    def settings(self):
        s = BotSettings()
        s.min_volume, s.max_volume = 500, 30000
        return s

    def test_same_subject_as_written_post_is_skipped(self):
        conn = memory_db([("자동차정기검사비용", 5380, "used", None), ("자동차정기검사조회", 9820, "new", None),
                          ("자동차종합검사비용", 6500, "new", None)])
        picked = [c["keyword"] for c in ranked_candidates(conn, self.settings())]
        self.assertNotIn("자동차정기검사조회", picked)
        self.assertIn("자동차종합검사비용", picked)

    def test_difficulty_estimated_from_same_head(self):
        conn = memory_db([("조리사자격증", 60000, "new", None), ("한식조리사자격증", 5000, "new", 0.65),
                          ("한식조리사자격증시험일정", 2000, "new", None)])
        rows = {c["keyword"]: c for c in ranked_candidates(conn, self.settings())}
        self.assertIn("추정", rows["한식조리사자격증시험일정"]["comp_info"])


class FactCheckTest(unittest.TestCase):
    def fake(self, checks, html):
        llm = SimpleNamespace(generate=lambda *a, **k: json.dumps({"checks": checks, "edits": 1, "content_html": html}))
        return llm

    def test_softened_is_not_review_blocking(self):
        original = "<p>" + "가" * 200 + "</p>"
        checks = [{"claim": "a", "verdict": "softened"}] * 4 + [{"claim": "b", "verdict": "unverified"}]
        fc = factcheck.verify(self.fake(checks, original), "k", original)
        self.assertEqual(len(fc.softened), 4)
        self.assertFalse(fc.needs_review)
        self.assertIn("완화 4", fc.summary)

    def test_too_short_review_keeps_original(self):
        original = "<p>" + "가" * 200 + "</p>"
        fc = factcheck.verify(self.fake([], "<p>짧음</p>"), "k", original)
        self.assertEqual(fc.content_html, original)


class ConsistencyTest(unittest.TestCase):
    BODY = ("<p>우체국 방문택배 요금은 5,000원에서 14,000원이에요. 미리 결제하면 한 건에 500원 할인돼요. "
            "집배원이 무게를 다시 재요.</p><table><tr><td>4구간</td><td>14,000원</td></tr></table>")

    def article(self, title, excerpt):
        from bot.writer import Article
        return Article(title=title, excerpt=excerpt, content_html=self.BODY, tags=[])

    def test_excerpt_mismatch_replaced_from_body(self):
        from bot.writer import align_with_body, numbers_match
        a = self.article("2026 우체국 방문택배 요금", "요금은 5,000~13,000원이고 500원 할인돼요.")
        self.assertEqual(align_with_body(a), ["요약문"])
        self.assertTrue(numbers_match(a.excerpt, self.BODY))
        self.assertLessEqual(len(a.excerpt), 120)

    def test_consistent_untouched_and_title_flagged(self):
        from bot.writer import align_with_body
        ok = self.article("우체국 방문택배 요금 정리 (500원 할인)", "요금은 5,000~14,000원이에요.")
        self.assertEqual(align_with_body(ok), [])
        bad = self.article("우체국 방문택배 요금 13,000원까지", "요금은 5,000~14,000원이에요.")
        self.assertEqual(align_with_body(bad), ["제목(확인 필요)"])

    def test_reviewer_title_excerpt_parsed(self):
        original = "<p>" + "가" * 200 + "</p>"
        llm = SimpleNamespace(generate=lambda *a, **k: json.dumps(
            {"checks": [], "edits": 0, "title": "새 제목", "excerpt": "새 요약", "content_html": original}))
        fc = factcheck.verify(llm, "k", original, "옛 제목", "옛 요약")
        self.assertEqual((fc.title, fc.excerpt), ("새 제목", "새 요약"))


class PublishQueueTest(unittest.TestCase):
    def test_pending_uses_content_hash(self):
        import tempfile
        from bot import publish_queue
        with tempfile.TemporaryDirectory() as tmp:
            q, done = Path(tmp, "queue"), Path(tmp, "done")
            q.mkdir(); done.mkdir()
            f = q / "a.json"
            f.write_text('{"keyword": "x"}', encoding="utf-8")
            with mock.patch.object(publish_queue, "QUEUE_DIR", q), mock.patch.object(publish_queue, "DONE_DIR", done):
                self.assertEqual(publish_queue.pending(), [f])
                publish_queue._marker(f).touch()
                self.assertEqual(publish_queue.pending(), [])
                f.write_text('{"keyword": "x", "v": 2}', encoding="utf-8")  # 내용이 바뀌면 다시 올라감
                self.assertEqual(publish_queue.pending(), [f])


class DirectRefreshTest(unittest.TestCase):
    POST = {"id": 59, "title": "t", "url": "u", "siblings": [("a", 100)]}

    def test_direct_mode_only_plans(self):
        s = BotSettings.from_option({"write_mode": "direct"}, {})
        state = {"refresh": {}}
        with mock.patch.object(refresh, "pick_targets", return_value=[(self.POST, "enhance", "정체")]), \
                mock.patch.object(refresh, "refresh_post") as api_call, mock.patch.object(refresh, "connect"):
            logs = refresh.run_weekly(s, state)
        api_call.assert_not_called()  # API 로 고치지 않음
        self.assertEqual(state["refresh_target"]["id"], 59)
        self.assertEqual(state["refresh_target"]["siblings"], ["a"])
        self.assertIn("대상 준비", logs[0])

    def fake_wp(self, modified="2026-11-04 10:00:00", content="<p>" + "가" * 100 + "</p>"):
        calls = []

        def wp(*args):
            calls.append(args)
            field = next((a for a in args if a.startswith("--field=")), "")
            return {"--field=post_modified": modified, "--field=post_content": content,
                    "--field=post_title": "제목"}.get(field, "")
        return wp, calls

    def test_apply_direct(self):
        wp, calls = self.fake_wp()
        state = {"refresh": {}, "refresh_target": {"id": 59, "why": "정체"}}
        data = {"post_id": 59, "base_modified": "2026-11-04 10:00:00", "content_html": "<p>" + "나" * 120 + "</p>"}
        with mock.patch.object(refresh, "wp_cli", side_effect=wp):
            msg = refresh.apply_direct(data, state)
        self.assertTrue(any(a[:2] == ("post", "update") for a in calls))
        self.assertNotIn("refresh_target", state)
        self.assertEqual(sum(state["refresh"].values()), 1)
        self.assertIn("정체", msg)

    def test_apply_direct_refuses_conflict_and_shrink(self):
        for modified, html in (("2026-11-05 09:00:00", "<p>" + "나" * 120 + "</p>"),  # 그 사이 수정됨
                               ("2026-11-04 10:00:00", "<p>짧음</p>")):                  # 본문이 크게 줄어듦
            wp, calls = self.fake_wp(modified=modified)
            data = {"post_id": 59, "base_modified": "2026-11-04 10:00:00", "content_html": html}
            with mock.patch.object(refresh, "wp_cli", side_effect=wp), self.assertRaises(RuntimeError):
                refresh.apply_direct(data, {"refresh": {}})
            self.assertFalse(any(a[:2] == ("post", "update") for a in calls))


class ScheduleTest(unittest.TestCase):
    def test_publish_oldest_ready_draft(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot import schedule
        calls = []

        def wp(*args):
            calls.append(args)
            if args[:2] == ("post", "list"):
                return "80\n85\n"
            return "퇴직금 글" if "--field=post_title" in args else ""
        with mock.patch.object(schedule, "wp_cli", side_effect=wp):
            msg = schedule.publish_next(datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")))
        update = next(a for a in calls if a[:2] == ("post", "update"))
        self.assertEqual(update[2], "80")  # 가장 오래된 것
        self.assertIn("--post_date_gmt=2026-11-05 00:00:00", update)
        self.assertIn("대기 1편", msg)
        with mock.patch.object(schedule, "wp_cli", return_value=""):
            self.assertIsNone(schedule.publish_next(datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))))

    def test_publish_skips_post_still_waiting_for_photos(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot import schedule
        calls = []

        def wp(*args):
            calls.append(args)
            if args[:2] == ("post", "list"):
                return "80\n85\n"
            if args[:3] == ("post", "meta", "get"):
                return '[{"prompt": "x"}]' if args[3] == "80" else ""  # 80 은 사진 대기 중
            return "글" if "--field=post_title" in args else ""
        with mock.patch.object(schedule, "wp_cli", side_effect=wp):
            schedule.publish_next(datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertEqual(next(a for a in calls if a[:2] == ("post", "update"))[2], "85")

    def test_quota_exceeded_saves_scene_for_later(self):
        from bot import images, photo
        calls = []
        with mock.patch.object(images, "cloudflare_image", side_effect=images.QuotaExceeded("한도")), \
             mock.patch.object(photo, "wp_cli", side_effect=lambda *a: calls.append(a) or ""):
            note = photo.make_or_wait(219, {"prompt": "a calm lake", "alt": "호수"}, ("a", "t"))
        self.assertIn("오전 9시", note)
        self.assertIn(("post", "meta", "update", "219", photo.PENDING_META, '[{"prompt": "a calm lake", "alt": "호수"}]'), calls)

    def test_first_scene_skips_text_prone(self):
        from bot import photo
        scene = photo.first_scene(["a printed contract on a desk", "a quiet street"], ["계약서", "거리"])
        self.assertEqual(scene, {"prompt": "a quiet street", "alt": "거리"})

    def test_settings(self):
        s = BotSettings.from_option({"post_status": "schedule", "schedule_times": "9:00\n18시"}, {})
        self.assertEqual((s.post_status, s.schedule_times), ("schedule", ["09:00", "18:00"]))
        self.assertEqual(BotSettings.from_option({}, {}).schedule_times, ["09:00"])


class DirectRoutineTest(unittest.TestCase):
    def test_due_slot(self):
        from datetime import datetime
        from bot.schedule import due_slot
        times = ["07:40", "13:40"]
        self.assertIsNone(due_slot(times, [], datetime(2026, 11, 5, 7, 39)))
        self.assertEqual(due_slot(times, [], datetime(2026, 11, 5, 7, 40)), "07:40")
        self.assertIsNone(due_slot(times, ["07:40"], datetime(2026, 11, 5, 9, 0)))
        self.assertIsNone(due_slot(times, [], datetime(2026, 11, 5, 11, 0)))  # 3시간 넘게 지난 시각은 건너뜀
        self.assertEqual(due_slot(times, ["07:40"], datetime(2026, 11, 5, 13, 41)), "13:40")

    def test_fire_without_token_is_logged_not_crash(self):
        from bot import run
        state = {"log": []}
        run.fire_routine(state, BotSettings.from_option({}, {}), "직접 작성 07:40")
        self.assertFalse(state["log"][0]["ok"])
        self.assertIn("CLAUDE_ROUTINE_TOKEN", state["log"][0]["msg"])
        self.assertNotIn("직접 작성 글:", state["log"][0]["msg"])  # 예비 봇 판단에 섞이지 않음

    def test_skip_file_rejects_keywords(self):
        from bot import publish_queue
        conn = memory_db([("알뜰폰무제한요금제", 3370, "new", None), ("하이패스카드신청", 5000, "new", None)])
        with mock.patch.object(publish_queue, "connect", return_value=conn):
            msg = publish_queue.skip_keywords({"type": "skip", "keywords": [{"keyword": "알뜰폰무제한요금제", "why": "상품 비교"}]})
        self.assertIn("1개", msg)
        row = conn.execute("SELECT status, reason FROM keywords WHERE keyword='알뜰폰무제한요금제'").fetchone()
        self.assertEqual(row["status"], "rejected")
        self.assertIn("상품 비교", row["reason"])
        picked = [c["keyword"] for c in ranked_candidates(conn, SelectionTest().settings())]
        self.assertNotIn("알뜰폰무제한요금제", picked)


class QualityTest(unittest.TestCase):
    def test_official_sources_section(self):
        from bot import sources
        items = [{"name": "공단 수수료", "url": "https://main.kotsa.or.kr/a"},
                 {"name": "블로그", "url": "https://x.tistory.com/1"},
                 {"name": "깨진 링크", "url": "https://www.gangdong.go.kr/broken"},
                 {"name": "<생활법령>", "url": "https://easylaw.go.kr/b"}]
        html, dropped = sources.section(items, check=lambda u: "broken" not in u)
        self.assertIn("공식 안내 바로가기", html)
        self.assertIn("main.kotsa.or.kr", html)
        self.assertIn("&lt;생활법령&gt;", html)  # 이름은 이스케이프
        self.assertNotIn("tistory", html)
        self.assertEqual(len(dropped), 2)
        self.assertEqual(sources.section([{"name": "x", "url": "http://a.go.kr"}], check=lambda u: True)[0], "")  # https 만
        for url in ("https://www.korea.kr/news/x", "https://www.airport.kr/ap_ko/906/subview.do"):
            self.assertTrue(sources.is_official(url), url)  # 주소 끝이 .go.kr 이 아닌 공식 기관
        self.assertFalse(sources.is_official("https://notkorea.kr/a"))

    def test_low_ctr_post_gets_retitle(self):
        conn = memory_db([])
        conn.executescript(refresh.GSC_SCHEMA)
        url = "https://first-lab.kr/a/"
        conn.execute("INSERT INTO gsc_page_history VALUES ('2026-11-04', ?, 2, 300, 6.0)", (url,))
        posts = json.dumps([{"id": 7, "title": "t", "url": url, "date": "2026-09-01", "refreshed": "", "has_number": True}])
        with mock.patch.object(refresh, "wp_cli", return_value=posts):
            picks = refresh.pick_targets(conn, 1)
        self.assertEqual(picks[0][1], "retitle")

    def test_retitle_numbers_must_be_in_body(self):
        body = "<p>승용차 정기검사 수수료는 23,000원이에요.</p>"
        self.assertEqual(refresh._checked_title({"title": "정기검사 비용 23,000원 정리"}, body)[0], "정기검사 비용 23,000원 정리")
        with self.assertRaises(RuntimeError):
            refresh._checked_title({"title": "정기검사 비용 25,000원"}, body)


class ReviewGateTest(unittest.TestCase):
    def test_gate(self):
        from bot.review_gate import problems
        ok = [{"claim": "수수료 23,000원", "urls": ["https://main.kotsa.or.kr/a"], "verdict": "맞음", "action": "유지"},
              {"claim": "철회 불가", "urls": [], "verdict": "확인불가", "action": "완화"}]
        self.assertEqual(problems(None), ["검수 기록 없음"])
        ok5 = ok + [{"claim": f"항목 {i}", "urls": ["https://a.go.kr"], "verdict": "맞음", "action": "유지"} for i in range(3)]
        fixed = ok5 + [{"claim": "고친 주장", "urls": ["https://www.gov.kr/x"], "verdict": "틀림", "action": "수정"}]
        self.assertEqual(problems(fixed), [])  # 틀려서 공식 출처(정부24)대로 고친 것은 통과
        self.assertEqual(problems(ok5, "검사 비용 23,000원", "수수료 23,000원이에요"), [])
        self.assertTrue(any("25000" in p for p in problems(ok5, "검사 비용 25,000원")))  # 제목 숫자가 검수 안 됨
        self.assertTrue(any("최소" in p for p in problems(ok)))  # 주장 2개뿐
        bad = [{"claim": "75만원", "urls": ["https://x.tistory.com/1"], "verdict": "맞음", "action": "유지"},
               {"claim": "철회 불가", "urls": ["https://a.go.kr"], "verdict": "확인불가", "action": "유지"}]
        self.assertEqual(len(problems(bad)), 3)  # 비공식 출처, 확인불가인데 유지, 주장 수 부족


class HoldFixTest(unittest.TestCase):
    BODY = "<p>" + "수수료는 23,000원이에요. " * 20 + "</p>"

    def run_fix(self, review):
        from bot import hold, schedule
        calls = []

        def wp(*args):
            calls.append(args)
            field = next((a for a in args if a.startswith("--field=")), "")
            return {"--field=post_status": "draft", "--field=post_modified": "2026-11-04 10:00:00",
                    "--field=post_content": self.BODY, "--field=post_title": "검사 비용 23,000원",
                    "--field=post_excerpt": "수수료 23,000원"}.get(field, "")
        with mock.patch.object(hold, "wp_cli", side_effect=wp), mock.patch.object(schedule, "wp_cli", side_effect=wp):
            msg = hold.apply_fix({"post_id": 144, "base_modified": "2026-11-04 10:00:00",
                                  "content_html": self.BODY, "review": review})
        return msg, calls

    def test_pass_goes_to_queue(self):
        review = [{"claim": f"수수료 23,000원 {i}", "urls": ["https://main.kotsa.or.kr/a"], "verdict": "맞음", "action": "유지"}
                  for i in range(5)]
        msg, calls = self.run_fix(review)
        self.assertIn("공개 대기열", msg)
        self.assertIn(("post", "meta", "update", "144", "_firstlab_queue", "ready"), calls)

    def test_fail_stays_held(self):
        msg, calls = self.run_fix([{"claim": "x", "urls": [], "verdict": "확인불가", "action": "유지"}])
        self.assertIn("계속 보류", msg)
        self.assertFalse(any(c[:3] == ("post", "meta", "update") and "_firstlab_queue" in c for c in calls))


class ContinuousTest(unittest.TestCase):
    def run_step(self, state, minute, on=True, fire_ok=True):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot import run
        now = datetime(2026, 11, 5, 10, minute, tzinfo=ZoneInfo("Asia/Seoul"))
        settings = BotSettings.from_option({}, {})
        with mock.patch.object(run, "continuous_on", return_value=on), \
                mock.patch.object(run, "set_continuous") as stop, \
                mock.patch.object(run, "fire_routine", return_value=fire_ok) as fire:
            run.continuous_step(state, settings, now)
        return fire, stop

    def test_one_at_a_time_then_next(self):
        state = {"log": []}
        fire, _ = self.run_step(state, 0)
        self.assertEqual(fire.call_count, 1)
        fire, _ = self.run_step(state, 20)  # 아직 안 올라옴 → 기다림
        self.assertEqual(fire.call_count, 0)
        state["log"].insert(0, {"time": "2026-11-05 10:25", "ok": True, "msg": "직접 작성 글: [초안 저장] 'x'"})
        fire, _ = self.run_step(state, 26)  # 올라옴 → 다음 편
        self.assertEqual(fire.call_count, 1)
        self.assertEqual(state["continuous"]["count"], 1)

    def test_stops_after_repeated_failures(self):
        state = {"log": [], "continuous": {"fired_at": "2026-11-05 08:00", "fails": 2, "count": 3}}
        fire, stop = self.run_step(state, 0)  # 60분 넘게 안 올라옴 → 3번째 실패 → 정지
        fire.assert_not_called()
        stop.assert_called_once_with(False)
        self.assertFalse(state["log"][0]["ok"])

    def test_off_clears_state(self):
        state = {"log": [], "continuous": {"fired_at": "2026-11-05 10:00"}}
        fire, _ = self.run_step(state, 5, on=False)
        fire.assert_not_called()
        self.assertNotIn("continuous", state)


class PlanTest(unittest.TestCase):
    def test_publish_plan_jitter(self):
        from bot.schedule import plan_times
        plan = plan_times(["09:00", "18:00"], 30, "2026-11-05")
        self.assertEqual(plan, plan_times(["18:00", "09:00"], 30, "2026-11-05"))  # 하루 동안 같은 값
        h, m = map(int, plan[0].split(":"))
        self.assertTrue(8 * 60 + 30 <= h * 60 + m <= 9 * 60 + 30)
        self.assertEqual(plan_times(["09:00"], 0, "2026-11-05"), ["09:00"])
        self.assertEqual(plan_times(["00:05"], 30, "2026-11-05")[0][:2], "00")  # 전날로 넘어가지 않음

    def test_direct_enabled_setting(self):
        self.assertTrue(BotSettings.from_option({}, {}).direct_enabled)  # 기존 설정(값 없음)은 켜짐
        self.assertFalse(BotSettings.from_option({"direct_enabled": "0"}, {}).direct_enabled)
        self.assertEqual(BotSettings.from_option({"direct_times": "7:40\n13:40"}, {}).direct_times, ["07:40", "13:40"])

    def test_write_mode_setting(self):
        self.assertEqual(BotSettings.from_option({}, {}).write_mode, "direct")
        self.assertEqual(BotSettings.from_option({"write_mode": "bot"}, {}).write_mode, "bot")


class MiscTest(unittest.TestCase):
    def test_competition_from_urls(self):
        comp = competition.from_urls(["https://www.q-net.or.kr/a", "https://x.tistory.com/1", "https://namu.wiki/w/a"])
        self.assertEqual(comp.strong, 2)
        self.assertIsNone(competition.from_urls(["https://a.com/1"]))

    def test_patch_content_with_inline_tags(self):
        html = "<p>앞</p><p><strong>강아지</strong> 전국 평균은 17만 원대예요.</p><p>뒤</p>"
        out = patch_content(html, {"find": "전국 평균은 17만", "replace_paragraph": "바뀐 문단"})
        self.assertEqual(out, "<p>앞</p><p>바뀐 문단</p><p>뒤</p>")
        self.assertIsNone(patch_content(out, {"find": "전국 평균은 17만", "replace_paragraph": "x"}))

    def test_calculator_line_becomes_shortcode_block(self):
        from bot.blocks import to_blocks
        out = to_blocks('<p>앞</p>\n<p>[firstlab_calc id="car-inspection"]</p>\n<p>문장 속 [firstlab_calc id="x"] 그대로</p>')
        self.assertIn('<!-- wp:shortcode -->\n[firstlab_calc id="car-inspection"]\n<!-- /wp:shortcode -->', out)
        self.assertIn("<p>문장 속 [firstlab_calc", out)

    def test_patches_file_valid(self):
        for p in json.loads((ROOT / "wordpress" / "patches.json").read_text(encoding="utf-8")):
            self.assertTrue({"post", "why", "find"} <= set(p))
            self.assertTrue({"replace_paragraph", "insert_after"} & set(p))

    def test_patches_apply_only_once(self):
        """교체 문장에 find 가 남아 있으면 배포마다 다시 고쳐지므로 금지."""
        for p in json.loads((ROOT / "wordpress" / "patches.json").read_text(encoding="utf-8")):
            if "replace_paragraph" in p:
                self.assertNotIn(p["find"], p["replace_paragraph"], p["why"])

    def test_patch_list_item(self):
        html = "<ul><li>앞</li><li><strong>철회</strong>: 대상이 아니에요.</li></ul>"
        out = patch_content(html, {"tag": "li", "find": "대상이 아니에요", "replace_paragraph": "완화"})
        self.assertEqual(out, "<ul><li>앞</li><li>완화</li></ul>")

    def test_patch_insert_calculator_after_paragraph(self):
        html = "<!-- wp:paragraph -->\n<p>과태료는 <strong>4만</strong>원부터</p>\n<!-- /wp:paragraph -->\n\n<!-- wp:heading -->"
        patch = {"find": "과태료는", "insert_after": '[firstlab_calc id="car-inspection"]'}
        out = patch_content(html, patch)
        self.assertIn('<!-- /wp:paragraph -->\n\n<!-- wp:shortcode -->\n[firstlab_calc id="car-inspection"]', out)
        self.assertTrue(out.endswith("<!-- wp:heading -->"))
        self.assertIsNone(patch_content(out, patch))  # 이미 넣었으면 다시 넣지 않음

    def test_internal_links_kept_regardless_of_encoding(self):
        from bot.related import clean_links
        allowed = {"https://first-lab.kr/%ec%9e%90%eb%8f%99%ec%b0%a8/", "https://first-lab.kr/car-inspection-calculator/"}
        html = ('<p><a href="https://first-lab.kr/자동차">보험</a> <a href="https://first-lab.kr/car-inspection-calculator/">계산기</a> '
                '<a href="https://example.com/x">외부</a></p>')
        out, kept = clean_links(html, allowed)
        self.assertEqual(kept, 2)
        self.assertNotIn("example.com", out)
        self.assertIn("> 외부</p>", out)  # 외부 링크는 풀고 문구만 남김

    def test_settings_defaults(self):
        s = BotSettings.from_option({}, {})
        self.assertEqual(s.daily_cost_cap, 3)
        self.assertEqual(BotSettings.from_option({"daily_cost_cap": "0"}, {}).daily_cost_cap, 0)

    def test_site_author_id(self):
        from bot import publisher
        with mock.patch.object(publisher, "wp_cli", return_value="1\n3\n"):
            self.assertEqual(publisher.site_author_id(), "1")
        with mock.patch.object(publisher, "wp_cli", return_value=""):
            self.assertEqual(publisher.site_author_id(), "")

    def test_refresh_siblings(self):
        conn = memory_db([("자동차정기검사비용", 5380, "used", None), ("자동차정기검사조회", 9820, "new", None),
                          ("자동차정기검사기간", 3000, "new", None), ("자동차종합검사비용", 6500, "new", None)])
        url = "https://first-lab.kr/" + urllib.parse.quote("자동차정기검사비용") + "/"
        self.assertEqual([k for k, _ in refresh.siblings(conn, url)], ["자동차정기검사조회", "자동차정기검사기간"])

    def test_popular_page_sums_clicks_per_page(self):
        rows = [{"page": "a", "clicks": 3}, {"page": "b", "clicks": 4}, {"page": "a", "clicks": 3}]
        self.assertEqual(search_console.top_page(rows, min_clicks=5), "a")
        self.assertEqual(search_console.top_page(rows, min_clicks=7), "")  # 클릭이 적으면 최신 글이 대표
        self.assertEqual(search_console.top_page([], min_clicks=1), "")

    def test_text_prone_image_prompts_skipped(self):
        from bot import images
        self.assertEqual(images.text_prone("a printed loan payment schedule on a desk"), "printed")
        self.assertEqual(images.text_prone("a toll gate with green overhead signs"), "signs")
        self.assertEqual(images.text_prone("a quiet street in Jeju with stone walls on a wooden table"), "")

    def test_migrate_moves_body_photos_out(self):
        from bot import migrate_photos
        url = "https://first-lab.kr/wp-content/uploads/2026/11/{}.jpg"
        body = ('<!-- wp:paragraph -->\n<p>도입</p>\n<!-- /wp:paragraph -->\n\n'
                '<!-- wp:image {"sizeSlug":"large"} -->\n<figure class="wp-block-image size-large"><img src="'
                + url.format("ai-aaaaaaaaaa") + '" alt="a"/></figure>\n<!-- /wp:image -->\n\n'
                '<!-- wp:paragraph -->\n<p>중간</p>\n<!-- /wp:paragraph -->\n\n'
                '<!-- wp:image {"sizeSlug":"large"} -->\n<figure class="wp-block-image size-large"><img src="'
                + url.format("ai-bbbbbbbbbb") + '" alt="b"/></figure>\n<!-- /wp:image -->\n\n'
                '<!-- wp:paragraph -->\n<p>끝</p>\n<!-- /wp:paragraph -->')
        self.assertEqual(migrate_photos.body_photos(body), ["ai-aaaaaaaaaa", "ai-bbbbbbbbbb"])
        out = migrate_photos.strip_photos(body)
        self.assertNotIn("wp:image", out)
        self.assertEqual([p for p in ("<p>도입</p>", "<p>중간</p>", "<p>끝</p>") if p in out], ["<p>도입</p>", "<p>중간</p>", "<p>끝</p>"])
        self.assertEqual(migrate_photos.body_photos(out), [])  # 다시 실행해도 아무 일 없음

    def test_migrate_keeps_body_when_no_photo_in_media(self):
        from bot import images, migrate_photos, photo
        body = ('<!-- wp:image {"sizeSlug":"large"} -->\n<figure class="wp-block-image size-large"><img src="'
                'https://first-lab.kr/wp-content/uploads/2026/11/ai-aaaaaaaaaa.jpg" alt="a"/></figure>\n<!-- /wp:image -->')
        calls = []
        with mock.patch.object(images, "attachment_id", return_value=""), \
             mock.patch.object(photo, "meta", return_value=""), \
             mock.patch.object(migrate_photos, "wp_cli", side_effect=lambda *a: calls.append(a) or ""):
            msg = migrate_photos.migrate_post("80", body)
        self.assertIn("그대로 둠", msg)
        self.assertFalse(any(c[:2] == ("post", "update") for c in calls))  # 대표 사진을 못 정하면 본문을 지우지 않음

    def test_attachment_lookup_includes_inherit_status(self):
        from bot import images
        with mock.patch.object(images, "wp_cli", return_value="501\n") as cli:
            self.assertEqual(images.attachment_id("ai-aaaaaaaaaa"), "501")
        self.assertIn("--post_status=inherit", cli.call_args[0])  # 첨부는 inherit 상태라 빼면 못 찾음


class GateStructureNumberTest(unittest.TestCase):
    def test_step_counts_in_title_are_not_facts(self):
        from bot import review_gate
        review = [{"claim": f"등록 기한 30일, 과태료 100만 원 {i}", "urls": ["https://www.animal.go.kr/a"],
                   "verdict": "맞음", "action": "유지"} for i in range(5)]
        self.assertEqual(review_gate.problems(review, "반려동물 등록 방법 4단계, 30일 기한·과태료 100만 원"), [])
        # 사실 숫자는 계속 확인: 기한이 검수 안 된 값이면 막힘
        self.assertTrue(review_gate.problems(review, "반려동물 등록 4단계, 60일 기한"))


class ImageCheckTest(unittest.TestCase):
    """대표 사진 검사 반영 (bot/image_check.py)."""

    def run_check(self, data, current="ai-aaaaaaaaaa", rounds="0"):
        from bot import image_check, photo
        calls = []
        metas = {photo.PHOTO_META: current, image_check.ROUND_META: rounds}

        def fake_cli(*args):
            calls.append(args)
            if args[:3] == ("post", "meta", "get"):
                return metas.get(args[4], "")
            return ""
        made = []
        with mock.patch.object(image_check, "wp_cli", side_effect=fake_cli), \
             mock.patch.object(photo, "wp_cli", side_effect=fake_cli), \
             mock.patch.object(image_check, "load_settings", return_value=mock.Mock(cf_account_id="a", cf_api_token="t")), \
             mock.patch.object(photo, "make", side_effect=lambda pid, scene, cf: made.append(scene) or "ai-cccccccccc"), \
             mock.patch.object(image_check, "_to_title_card") as card:
            msg = image_check.apply_check({"type": "image_check", "post_id": 176, **data})
        return msg, calls, made, card

    def test_clean_post_marked_checked(self):
        msg, calls, made, card = self.run_check({"replace": [], "remove": []})
        self.assertIn(("post", "meta", "update", "176", "_firstlab_img_checked", "1"), calls)
        self.assertEqual(made, [])
        self.assertIn("이상 없음", msg)

    def test_replace_makes_new_photo_and_rechecks(self):
        fix = {"old": "ai-aaaaaaaaaa", "prompt": "a quiet street with trees", "alt": "가로수 길", "why": "글자"}
        msg, calls, made, card = self.run_check({"replace": [fix]})
        self.assertEqual(made, [fix])
        self.assertIn(("post", "meta", "update", "176", "_firstlab_img_round", "1"), calls)  # 새 사진도 다시 검사
        self.assertFalse(any("_firstlab_img_checked" in c for c in calls))

    def test_after_max_rounds_photo_becomes_title_card(self):
        fix = {"old": "ai-aaaaaaaaaa", "prompt": "a quiet street with trees", "alt": "x", "why": "글자"}
        msg, calls, made, card = self.run_check({"replace": [fix]}, rounds="2")
        self.assertEqual(made, [])
        card.assert_called_once()
        self.assertIn(("post", "meta", "update", "176", "_firstlab_img_checked", "1"), calls)

    def test_text_prone_new_scene_becomes_title_card(self):
        fix = {"old": "ai-aaaaaaaaaa", "prompt": "a printed contract on a desk", "alt": "x", "why": "글자"}
        _, _, made, card = self.run_check({"replace": [fix]})
        self.assertEqual(made, [])
        card.assert_called_once()

    def test_add_photo_when_none(self):
        add = [{"prompt": "a puppy on a sunny lawn", "alt": "잔디밭 강아지"}]
        msg, calls, made, card = self.run_check({"add": add}, current="")
        self.assertEqual(made, [{"prompt": "a puppy on a sunny lawn", "alt": "잔디밭 강아지"}])
        self.assertIn("추가", msg)

    def test_stale_replace_for_other_photo_is_ignored(self):
        # 예전(본문 사진 시절) 요청: 지금 대표 사진이 아닌 사진 교체 → 대표 사진은 이상 없음으로 검사 끝
        fix = {"old": "ai-zzzzzzzzzz", "prompt": "a lake", "alt": "x", "why": "글자"}
        _, calls, made, _ = self.run_check({"replace": [fix]})
        self.assertEqual(made, [])
        self.assertIn(("post", "meta", "update", "176", "_firstlab_img_checked", "1"), calls)


if __name__ == "__main__":
    unittest.main()
