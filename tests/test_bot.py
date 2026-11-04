"""배포 전 자동 검사 (GitHub Actions). 서버·API 없이 도는 순수 로직만 확인한다.
실행: python -m unittest discover -s tests -v
"""
import json
import re
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

    def test_same_minute_previous_post_is_not_new(self):
        # 11/4 22:05: 직전 글 기록과 다음 편 시작이 같은 분 → 직전 글을 새 글로 착각해 두 편이 동시에 돎
        state = {"log": [{"time": "2026-11-05 10:05", "ok": True, "msg": "직접 작성 글: [공개 대기] '앞 글'"}]}
        fire, _ = self.run_step(state, 5)   # 10:05 다음 편 시작
        self.assertEqual(fire.call_count, 1)
        fire, _ = self.run_step(state, 6)   # 10:06 아직 새 글 없음 → 기다려야 함
        self.assertEqual(fire.call_count, 0)
        state["log"].insert(0, {"time": "2026-11-05 10:20", "ok": True, "msg": "직접 작성 글: [공개 대기] '새 글'"})
        fire, _ = self.run_step(state, 21)  # 새 글 올라옴 → 다음 편
        self.assertEqual(fire.call_count, 1)

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
            if "set_title" in p or "set_excerpt" in p:  # 제목·요약문 수정: 원래 값(from_…)이 있어야 한 번만 적용됨
                self.assertTrue({"post", "why"} <= set(p))
                self.assertTrue(("set_title" not in p or "from_title" in p) and ("set_excerpt" not in p or "from_excerpt" in p))
                continue
            self.assertTrue({"post", "why", "find"} <= set(p))
            self.assertTrue({"replace_paragraph", "insert_after", "remove_block", "remove_element"} & set(p))

    def test_patch_keeps_tag_attributes_and_removes_row(self):
        from bot.fix_posts import patch_content
        body = ('<h2 class="wp-block-heading">신청 방법 3가지 비교</h2>'
                '<table><tr><td>인터넷</td></tr><tr><td>전화</td><td>콜센터 1588-1300</td></tr></table>')
        out = patch_content(body, {"tag": "h2", "find": "신청 방법 3가지", "replace_paragraph": "신청 방법 비교"})
        self.assertIn('<h2 class="wp-block-heading">신청 방법 비교</h2>', out)
        out = patch_content(out, {"tag": "tr", "find": "<td>콜센터 1588-1300</td>", "remove_element": True})
        self.assertNotIn("1588-1300", out)
        self.assertIn("<td>인터넷</td>", out)
        self.assertEqual(patch_content("<pre>x</pre><p>x</p>", {"find": "x", "replace_paragraph": "y"}), "<pre>x</pre><p>y</p>")

    def test_patch_remove_block(self):
        from bot.fix_posts import patch_content
        body = ('<!-- wp:paragraph -->\n<p>앞</p>\n<!-- /wp:paragraph -->\n\n'
                '<!-- wp:table -->\n<figure class="wp-block-table"><table><tr><td>경차·소형</td><td>40~60만 원</td></tr></table></figure>\n<!-- /wp:table -->\n\n'
                '<!-- wp:paragraph -->\n<p>뒤</p>\n<!-- /wp:paragraph -->')
        out = patch_content(body, {"find": "<td>40~60만 원</td>", "remove_block": True})
        self.assertNotIn("wp:table", out)
        self.assertIn("<p>앞</p>", out)
        self.assertIn("<p>뒤</p>", out)
        self.assertIsNone(patch_content(out, {"find": "<td>40~60만 원</td>", "remove_block": True}))  # 한 번만

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

    def test_migrate_strips_photos_inside_html_blocks(self):
        # 보류 수정본은 figure 가 HTML 블록으로 저장됨 → 이것도 빼야 함 (11/4 #163 본문에 사진이 남은 문제)
        from bot import migrate_photos
        body = ('<!-- wp:paragraph -->\n<p>도입</p>\n<!-- /wp:paragraph -->\n\n'
                '<!-- wp:html -->\n<figure class="wp-block-image size-large"><img src="'
                'https://first-lab.kr/wp-content/uploads/2026/11/ai-639b039237.jpg" alt="논밭"/></figure>\n<!-- /wp:html -->\n\n'
                '<!-- wp:html -->\n<div class="note">사진 없는 HTML 블록</div>\n<!-- /wp:html -->\n\n'
                '<!-- wp:paragraph -->\n<p>끝</p>\n<!-- /wp:paragraph -->')
        self.assertEqual(migrate_photos.body_photos(body), ["ai-639b039237"])
        out = migrate_photos.strip_photos(body)
        self.assertNotIn("ai-639b039237", out)
        self.assertIn("사진 없는 HTML 블록", out)  # 다른 HTML 블록은 그대로
        self.assertIn("<p>끝</p>", out)

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
        args = cli.call_args[0]
        self.assertIn("--post_status=inherit", args)  # 첨부는 inherit 상태라 빼면 못 찾음
        self.assertIn("--meta_value=/ai-aaaaaaaaaa.jpg", args)  # 첨부 이름은 한국어 제목 → 파일 경로로 찾아야 함
        self.assertFalse(any(a.startswith("--name=") for a in args))


class DuplicateKeywordTest(unittest.TestCase):
    def test_same_keyword_is_not_published_twice(self):
        # 11/4: 동시 실행 두 갈래가 같은 후보로 글을 써서 '고용보험실업급여조건' 글이 두 편 생김
        import tempfile
        from bot import publish_queue
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "a.json")
            f.write_text(json.dumps({"keyword": "고용보험실업급여조건", "title": "t", "excerpt": "e",
                                     "content_html": "<p>x</p>", "tags": [], "category": "생활"}, ensure_ascii=False),
                         encoding="utf-8")
            with mock.patch.object(publish_queue, "existing_post_id", return_value="376"), \
                 mock.patch.object(publish_queue, "publish") as pub:
                msg = publish_queue.publish_file(f, mock.Mock(post_status="schedule"), set())
        pub.assert_not_called()
        self.assertIn("중복 키워드", msg)
        self.assertIn("376", msg)


class OfficialHostTest(unittest.TestCase):
    def test_exam_bodies_are_official(self):
        from bot.sources import is_official
        self.assertTrue(is_official("https://license.korcham.net/co/examguide.do?mm=21&cd=0103"))  # 컴활 시행기관
        self.assertTrue(is_official("https://www.q-net.or.kr/crf005.do"))
        self.assertFalse(is_official("https://blog.naver.com/x"))
        self.assertFalse(is_official("https://notkorcham.net/x"))  # 이름만 비슷한 주소는 아님


class DiscoverOncePerDayTest(unittest.TestCase):
    def test_second_run_same_day_is_skipped(self):
        import tempfile
        from bot import discover
        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp, "stamp")
            with mock.patch.object(discover, "STAMP_FILE", stamp):
                self.assertFalse(discover.already_ran_today("2026-11-05"))  # 표시 없음 → 실행
                stamp.write_text("2026-11-05")
                self.assertTrue(discover.already_ran_today("2026-11-05"))   # 오늘 이미 함 → 건너뜀
                self.assertFalse(discover.already_ran_today("2026-11-06"))  # 다음 날 → 실행


class SimilarTopicTest(unittest.TestCase):
    def run_similar(self, keyword, slugs):
        import urllib.parse
        from bot import publisher
        rows = [{"ID": i + 1, "post_name": urllib.parse.quote(s)} for i, s in enumerate(slugs)]
        with mock.patch.object(publisher, "wp_cli", return_value=json.dumps(rows)):
            return publisher.similar_post(keyword)

    def test_near_same_keyword_is_blocked(self):
        self.assertEqual(self.run_similar("대부업체대출조회", ["대부업대출조회", "고용보험가입기간"])[0], "1")

    def test_different_topics_pass(self):
        self.assertEqual(self.run_similar("해외여행준비물리스트", ["유럽여행준비물리스트", "베트남여행준비물"]), ("", ""))
        self.assertEqual(self.run_similar("고용보험가입기간", ["고용보험실업급여조건"]), ("", ""))


class GateCoverageTest(unittest.TestCase):
    def test_long_article_needs_claims_for_every_fact_sentence(self):
        from bot import review_gate
        body = "<p>" + "가" * 2100 + "</p>"  # 공백 뺀 2,100자 → 30개
        ok = [{"claim": f"사실 {i}", "urls": ["https://www.nts.go.kr/a"], "verdict": "맞음", "action": "유지"} for i in range(30)]
        self.assertEqual(review_gate.problems(ok, body_html=body), [])
        few = ok[:16]  # 11/4 자동차보험 글처럼 일부만 검수 → 막힘
        self.assertTrue(any("최소 30개" in p for p in review_gate.problems(few, body_html=body)))


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

    def test_text_prone_new_scene_keeps_photo_and_asks_again(self):
        fix = {"old": "ai-aaaaaaaaaa", "prompt": "a printed contract on a desk", "alt": "x", "why": "글자"}
        msg, calls, made, card = self.run_check({"replace": [fix]})
        self.assertEqual(made, [])
        card.assert_not_called()  # 장면 단어 때문에 사진을 빼지 않음 (11/4 #168)
        self.assertFalse(any("_firstlab_img_checked" in c for c in calls))  # 다음 검사에서 다시
        self.assertIn(("post", "meta", "update", "176", "_firstlab_img_round", "1"), calls)  # 반복은 MAX_ROUND 에서 멈춤

    def test_add_photo_when_none(self):
        add = [{"prompt": "a puppy on a sunny lawn", "alt": "잔디밭 강아지"}]
        msg, calls, made, card = self.run_check({"add": add}, current="")
        self.assertEqual(made, [{"prompt": "a puppy on a sunny lawn", "alt": "잔디밭 강아지"}])
        self.assertIn("추가", msg)

    def test_quota_turns_request_into_waiting_not_error(self):
        from bot import image_check, images, photo
        fix = {"old": "ai-aaaaaaaaaa", "prompt": "a quiet street", "alt": "거리", "why": "글자"}
        calls = []

        def fake_cli(*args):
            calls.append(args)
            return "ai-aaaaaaaaaa" if args[:3] == ("post", "meta", "get") and args[4] == photo.PHOTO_META else ""
        with mock.patch.object(image_check, "wp_cli", side_effect=fake_cli), \
             mock.patch.object(photo, "wp_cli", side_effect=fake_cli), \
             mock.patch.object(image_check, "load_settings", return_value=mock.Mock(cf_account_id="a", cf_api_token="t")), \
             mock.patch.object(photo, "make", side_effect=images.QuotaExceeded("한도")):
            msg = image_check.apply_check({"type": "image_check", "post_id": 176, "replace": [fix]})  # 예외 없이 끝나야 함
        self.assertIn("오전 9시", msg)
        wait = next(c for c in calls if c[:3] == ("post", "meta", "update") and c[4] == photo.PENDING_META)
        self.assertIn('"replace": "ai-aaaaaaaaaa"', wait[5])  # 사진 채우기가 이 사진을 바꿈

    def test_no_photo_is_never_marked_checked(self):
        _, calls, made, _ = self.run_check({"replace": [], "remove": []}, current="")
        self.assertEqual(made, [])
        self.assertFalse(any("_firstlab_img_checked" in c for c in calls))  # 사진 없는 글은 다음 검사에서 add

    def test_stale_replace_for_other_photo_is_ignored(self):
        # 예전(본문 사진 시절) 요청: 지금 대표 사진이 아닌 사진 교체 → 대표 사진은 이상 없음으로 검사 끝
        fix = {"old": "ai-zzzzzzzzzz", "prompt": "a lake", "alt": "x", "why": "글자"}
        _, calls, made, _ = self.run_check({"replace": [fix]})
        self.assertEqual(made, [])
        self.assertIn(("post", "meta", "update", "176", "_firstlab_img_checked", "1"), calls)



class UnreviewedTest(unittest.TestCase):
    """감시 세션 검수 목록: 글 속 계산기를 찾고, 검수 기록에 없는 계산기를 알려 준다."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import unreviewed
        self.u = unreviewed

    def test_finds_calc_in_saved_json(self):
        raw = json.dumps({"content_html": '<p>x</p>[firstlab_calc id="property-holding-tax"]'})
        self.assertEqual(self.u.embedded_calcs(raw), ["property-holding-tax"])

    def test_every_calculator_reviewed_or_listed(self):
        # 계산기 파일 이름이 기록 형식(calc:<이름>)과 맞아야 대조가 된다
        calcs = self.u.all_calcs()
        self.assertIn("inheritance-tax", calcs)
        self.assertTrue(self.u.reviewed_calcs() <= set(calcs), "reviewed.txt 의 calc: 이름이 실제 계산기에 없음")


class FaviconTest(unittest.TestCase):
    """파비콘이 없으면 구글 검색 결과에 지구본 기본 아이콘이 뜬다 (11/5 운영자 지적)."""

    ICONS = ROOT / "wordpress" / "themes" / "first-lab" / "assets" / "icons"

    def test_icon_files(self):
        for name in ("favicon.ico", "favicon.svg", "icon-192.png", "apple-touch-icon.png"):
            self.assertTrue((self.ICONS / name).stat().st_size > 0, name)
        # favicon.ico 머리: 예약 0, 형식 1(ico), 그림 수 3 (16·32·48px)
        head = (self.ICONS / "favicon.ico").read_bytes()[:6]
        self.assertEqual(head, bytes([0, 0, 1, 0, 3, 0]))

    def test_served_and_linked(self):
        setup = (ROOT / "server" / "wp-setup.sh").read_text(encoding="utf-8")
        self.assertIn('assets/icons/favicon.ico" "$WEB_ROOT/favicon.ico"', setup)
        site = (ROOT / "wordpress" / "mu-plugins" / "first-lab-site.php").read_text(encoding="utf-8")
        for name in ("/favicon.ico", "favicon.svg", "icon-192.png", "apple-touch-icon.png"):
            self.assertIn(name, site)


class SourceCheckTest(unittest.TestCase):
    """근거 대조(기록만): 글쓴이가 단 공식 링크에 주장 숫자가 실제로 있는지 (11/5 운영자 승인)."""

    REVIEW = [
        {"claim": "1·2차 동시 응시료 28,000원", "urls": ["https://www.q-net.or.kr/a"], "verdict": "맞음", "action": "유지"},
        {"claim": "민간자격 238건", "urls": ["https://www.pqi.or.kr/b"], "verdict": "맞음", "action": "유지"},
        {"claim": "상담 ARS 126-1-1", "urls": ["https://www.nts.go.kr/c"], "verdict": "맞음", "action": "유지"},
        {"claim": "수수료 13,700원", "urls": ["https://blog.naver.com/x"], "verdict": "맞음", "action": "유지"},
        {"claim": "확인 못 한 3,000원", "urls": ["https://www.q-net.or.kr/a"], "verdict": "확인불가", "action": "완화"},
    ]

    def test_statuses(self):
        from bot import source_check as sc
        pages = {"https://www.q-net.or.kr/a": sc.page_text("<p>1, 2차 동시 응시자: 28,000원</p>".encode(), "utf-8"),
                 "https://www.pqi.or.kr/b": None}
        result = sc.check(self.REVIEW, fetcher=lambda u: pages.get(u))
        # 공식 주소 없는 주장(블로그), 완화한 주장은 대조하지 않음. 못 연 페이지(pqi, nts) → '못 엶'
        self.assertEqual(result["counts"], {sc.FOUND: 1, sc.MISSING: 0, sc.UNOPENED: 2, sc.UNREADABLE: 0})
        self.assertEqual([c["claim"] for c in result["claims"]], ["민간자격 238건", "상담 ARS 126-1-1"])
        self.assertEqual(sc.key_numbers("5일 안에 12억 원"), [])  # 짧은 수는 어디에나 있어 제외
        self.assertIn("확인 1", sc.summary(result))
        self.assertEqual(sc.key_numbers("15억이면 691,200원"), [])  # 계산 결과는 출처에 없음
        self.assertEqual(sc.key_numbers("과표 1억 8,000만 원, 900,000원, 324,000원, 576,000원"), [])  # 계산 과정 나열

    def test_unreadable_source_for_plain_claim(self):
        """11/5 #484: 메뉴·화면 주장의 근거가 스크립트로 채우는 페이지(심평원 npay)뿐 → 글쓴이도 원문을 못 봤을 것."""
        from bot import source_check as sc
        review = [{"claim": "예방접종료 → 인플루엔자 선택", "urls": ["https://www.hira.or.kr/npay/index.do"], "verdict": "맞음", "action": "유지"},
                  {"claim": "주소지와 관계없이 접종", "urls": ["https://www.kdca.go.kr/a"], "verdict": "맞음", "action": "유지"}]
        result = sc.check(review, fetcher=lambda u: None if "hira" in u else "본문")
        self.assertEqual(result["counts"][sc.UNREADABLE], 1)
        self.assertEqual(result["claims"][0]["claim"], "예방접종료 → 인플루엔자 선택")

    def test_law_shell_to_body_url(self):
        from bot import source_check as sc
        shell = '<a href="/LSW/lsInfoP.do?lsiSeq=284381&amp;chrClsCd=010202&amp;urlMode=lsInfoP&amp;efYd=20260918">'
        self.assertEqual(sc.law_body_url(shell),
                         "https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=284381&chrClsCd=010202&urlMode=lsInfoP&efYd=20260918")

    def test_law_notation(self):
        from bot import source_check as sc
        self.assertTrue(sc.has_number("세율은 1천분의 5", "0.5"))     # 0.5%
        self.assertTrue(sc.has_number("100분의 20을 곱한", "20"))
        self.assertFalse(sc.has_number("1천분의 50", "0.5"))
        self.assertTrue(sc.has_number("총급여액 7천만원 이하", "7000"))  # 7,000만 원
        self.assertEqual(sc.readable_url("https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=1"),
                         "https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=1")

    def test_number_missing_from_page(self):
        from bot import source_check as sc
        page = sc.page_text("<p>응시수수료 14,300원, 2,800원</p>".encode(), "utf-8")
        result = sc.check(self.REVIEW[:1], fetcher=lambda u: page)
        self.assertEqual(result["claims"][0]["status"], sc.MISSING)  # 28000 이 280000·2800 안에 있다고 보면 안 됨
        self.assertEqual(result["claims"][0]["missing"], ["28000"])

    def test_never_blocks_publishing(self):
        from bot import source_check as sc
        saved = []
        with mock.patch.object(sc, "check", side_effect=RuntimeError("network")):
            msg = sc.record(1, self.REVIEW, lambda *a: saved.append(a))
        self.assertIn("실패", msg)
        self.assertEqual(saved, [])


class VerifyPatchesTest(unittest.TestCase):
    """감시: 고친 내용이 서버 글에 실제로 들어갔는지 (11/5 운영자 '최종 검토가 틀렸을 가능성' 질문 → 전수 확인 도구)."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import verify_patches
        self.v = verify_patches

    def post(self, body):
        return {"status": "draft", "title": "t", "excerpt": "e", "content_html": body}

    def test_detects_unapplied_and_accepts_applied(self):
        patches = [{"post": 1, "find": "옛 문장", "replace_paragraph": "새 문장이에요"}]
        self.assertEqual(len(self.v.problems(patches, {1: self.post("<p>옛 문장</p>")})), 1)
        self.assertEqual(self.v.problems(patches, {1: self.post("<p>새 문장이에요</p>")}), [])

    def test_table_cell_compared_with_tags(self):
        patches = [{"post": 1, "find": "<td>궐련</td><td>200개비</td>",
                    "replace_paragraph": "<td>궐련</td><td>200개비(확인)</td>", "tag": "tr"}]
        self.assertEqual(self.v.problems(patches, {1: self.post("<tr><td>궐련</td><td>200개비(확인)</td></tr>")}), [])

    def test_later_patch_supersedes(self):
        patches = [{"post": 1, "find": "A 문장", "replace_paragraph": "B 문장이에요"},
                   {"post": 1, "find": "B 문장이에요", "replace_paragraph": "C 문장이에요"}]
        self.assertEqual(self.v.problems(patches, {1: self.post("<p>C 문장이에요</p>")}), [])


class AuditSampleTest(unittest.TestCase):
    """주 1회 표본 감사: 숫자가 든 사실 문장만, 글마다 하나씩 (11/5 운영자 승인)."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import audit_sample
        self.a = audit_sample

    def test_sentences(self):
        body = ("<p>실손24에서 청구해요. 3만 원 이하는 영수증만 내면 돼요.</p>[firstlab_calc id=\"vat\"]"
                "<h2>공식 안내 바로가기</h2><ul><li>국세청 2026 안내 페이지 링크</li></ul>")
        self.assertEqual(self.a.sentences(body), ["3만 원 이하는 영수증만 내면 돼요."])

    def test_one_per_post_and_repeatable(self):
        posts = {i: {"title": str(i), "content_html": f"<p>신청 수수료는 한 번에 {i}만 원이에요. 신청 기한은 사유가 생긴 날부터 {i}일 안이에요.</p>"} for i in range(1, 6)}
        first = self.a.sample(posts, 3, "seed")
        self.assertEqual(len({pid for pid, _, _ in first}), 3)
        self.assertEqual(first, self.a.sample(posts, 3, "seed"))


class RegionalSourceGateTest(unittest.TestCase):
    """11/5 #482 도시가스: 전북 공급규정만 보고 '전출은 3일 전'을 전국 기준처럼 제목에 씀 → 관문이 막는다."""

    JEONBUK = "https://www.jeonbuk.go.kr/upload_data/board_data/BBS_0000017/1.pdf"
    CLAIM = {"claim": "폐지 3일 전 통지", "urls": [JEONBUK], "verdict": "맞음", "action": "유지"}

    def test_regional_only_number_in_title_is_held(self):
        from bot import review_gate as g
        out = g._regional_only([self.CLAIM], "도시가스 신청: 전출은 3일 전")
        self.assertEqual(len(out), 1)
        self.assertIn("전북", out[0])

    def test_passes_when_region_named_or_national_source(self):
        from bot import review_gate as g
        self.assertEqual(g._regional_only([self.CLAIM], "도시가스 신청: 전북은 전출 3일 전"), [])
        national = dict(self.CLAIM, urls=[self.JEONBUK, "https://www.law.go.kr/법령/도시가스사업법"])
        self.assertEqual(g._regional_only([national], "도시가스 신청: 전출은 3일 전"), [])

    def test_region_hosts(self):
        from bot import review_gate as g
        self.assertEqual(g.region_of("https://news.seoul.go.kr/env/archives/1"), ("서울",))
        self.assertIsNone(g.region_of("https://www.nts.go.kr/x"))  # 중앙부처 = 전국 자료
        self.assertIsNone(g.region_of("https://www.seoul.go.kr.evil.com/x"))


class ErrorTypeRegistryTest(unittest.TestCase):
    """고친 것마다 오류 유형이 있고, 유형마다 재발 방지(코드·테스트) 또는 검수로만 잡는 이유가 등록돼 있어야 한다
    (11/5 운영자: '수정한 모든 것에 재발 방지 대책'). 새 유형이면 automation/error_types.json 에 막는 방법부터 등록."""

    REG = json.loads((ROOT / "automation" / "error_types.json").read_text(encoding="utf-8"))
    PATCHES = json.loads((ROOT / "wordpress" / "patches.json").read_text(encoding="utf-8"))

    def test_every_fix_has_registered_type(self):
        for i, p in enumerate(self.PATCHES):
            self.assertIn(p.get("type"), self.REG, f"patches.json {i}번(글 {p['post']})에 등록된 오류 유형(type)이 없음")

    def test_every_type_has_prevention_that_exists(self):
        for key, t in self.REG.items():
            self.assertTrue(t.get("prevention") or t.get("review_only"), key)
            if not t.get("review_only"):
                self.assertTrue(t["checks"], f"{key}: 코드로 막는다면서 확인할 테스트가 없음")
            for ref in t["checks"]:
                path, token = ref.split("::", 1)
                self.assertIn(token, (ROOT / path).read_text(encoding="utf-8"), f"{key}: {ref} 가 실제로 없음")

    def test_common_errors_checklist_is_current(self):
        """글쓰기 독립 검수자가 읽는 '자주 틀린 유형' 표가 지금 수정 기록과 같아야 한다 (고친 뒤 다시 만들기)."""
        sys.path.insert(0, str(ROOT / "automation"))
        import common_errors
        self.assertEqual((ROOT / "automation" / "common-errors.md").read_text(encoding="utf-8"), common_errors.build(),
                         "python3 automation/common_errors.py 로 다시 만들기")
        self.assertIn("common-errors.md", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))

    def test_no_absence_wording_in_fixes(self):
        """확인 못 한 것을 '없다·찾지 못했다'로 고치지 않는다 (11/4 #219 파인 대출 계산기)."""
        sys.path.insert(0, str(ROOT / "automation"))
        from verify_patches import superseded
        for i, p in enumerate(self.PATCHES):
            text = p.get("replace_paragraph", "") + p.get("insert_after", "") + p.get("set_title", "") + p.get("set_excerpt", "")
            if re.search(r"찾지 못했|못 찾았|확인되지 않았", text) and not superseded(p, self.PATCHES[i + 1:]):
                self.fail(f"patches.json {i}번(글 {p['post']}): '없음' 단정 문구 — 확인처 안내로 쓰기")

if __name__ == "__main__":
    unittest.main()
