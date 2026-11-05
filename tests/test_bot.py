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
        """주제·검색 목적이 같으면 막고(기존 글 보강), 목적이 다르면 새 글 (11/5 운영자 '주휴수당 뜻이랑 계산은 다른 거잖아')."""
        conn = memory_db([("자동차정기검사비용", 5380, "used", None), ("자동차정기검사가격", 9820, "new", None),
                          ("자동차정기검사조회", 9000, "new", None), ("자동차종합검사비용", 6500, "new", None),
                          ("주휴수당계산", 5000, "used", None), ("주휴수당뜻", 8180, "new", None),
                          ("주휴수당계산방법", 3000, "new", None)])
        picked = [c["keyword"] for c in ranked_candidates(conn, self.settings())]
        self.assertNotIn("자동차정기검사가격", picked)   # 비용 = 비용 → 기존 글 보강
        self.assertNotIn("주휴수당계산방법", picked)     # 계산 = 계산
        self.assertIn("자동차정기검사조회", picked)      # 비용 ≠ 조회 → 새 글
        self.assertIn("주휴수당뜻", picked)              # 계산 ≠ 정의 → 새 글
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
        fixed = ok5 + [{"claim": "고친 주장", "urls": ["https://www.gov.kr/x"], "verdict": "틀림", "action": "수정", "rechecked": True}]
        self.assertEqual(problems(fixed), [])  # 틀려서 공식 출처(정부24)대로 고치고 검수자가 다시 확인한 것은 통과
        unchecked = ok5 + [{"claim": "고친 주장", "urls": ["https://www.gov.kr/x"], "verdict": "틀림", "action": "수정"}]
        self.assertTrue(any("재확인" in p for p in problems(unchecked)))  # 11/5 재확인 결과 전에 올린 일
        law_no_article = ok5 + [{"claim": "금액은 60%", "urls": ["https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=1"], "verdict": "맞음", "action": "유지"}]
        self.assertTrue(any("조문 번호" in p for p in problems(law_no_article)))  # 조문을 알아야 예외를 찾아 줌
        law_with_article = ok5 + [{"claim": "금액은 60% (고용보험법 제46조)", "urls": ["https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=1"], "verdict": "맞음", "action": "유지"}]
        self.assertEqual(problems(law_with_article), [])
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
                mock.patch.object(run, "fire_routine", return_value=fire_ok) as fire, \
                mock.patch.object(run, "save_state"):
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
                          ("자동차정기검사가격", 4000, "new", None), ("자동차정기검사요금", 3000, "new", None),
                          ("자동차종합검사비용", 6500, "new", None)])
        url = "https://first-lab.kr/" + urllib.parse.quote("자동차정기검사비용") + "/"
        # 같은 목적(비용)만 이 글의 보강 대상 — 조회는 따로 새 글
        self.assertEqual([k for k, _ in refresh.siblings(conn, url)], ["자동차정기검사가격", "자동차정기검사요금"])

    def test_gold_sibling_enhance_puts_keyword_in_title(self):
        """같은 주제·목적 황금 키워드는 새 글 대신 바로 보강, 제목에 그 말이 없으면 반영 안 함, 보강하면 그 키워드는 '사용됨'."""
        from bot import naver_hub
        self.assertTrue(refresh.title_has_keyword("주휴수당 뜻과 계산: 주 15시간 이상이면 받아요", "주휴수당뜻"))
        self.assertFalse(refresh.title_has_keyword("주휴수당 계산: 주 20시간 41,280원", "주휴수당뜻"))
        with mock.patch.object(naver_hub, "is_gold", side_effect=lambda sig, total, kw="": kw == "주휴수당의미"):
            self.assertEqual(refresh.gold_sibling({}, [("주휴수당개념", 100), ("주휴수당의미", 900)]), "주휴수당의미")
            self.assertEqual(refresh.gold_sibling({}, [("주휴수당개념", 100)]), "")
        state = {"refresh_target": {"id": 7, "gold_keyword": "주휴수당뜻", "why": "황금"}}
        wp = lambda *a: {"--field=post_modified": "m", "--field=post_content": "<p>" + "가" * 100 + "</p>",  # noqa: E731
                         "--field=post_title": "주휴수당 계산: 주 20시간 41,280원"}.get(a[-1], "")
        with mock.patch.object(refresh, "wp_cli", side_effect=wp), mock.patch.object(refresh, "_save"), \
                mock.patch.object(refresh, "_renew_evidence"):
            with self.assertRaises(RuntimeError):
                refresh.apply_direct({"post_id": 7, "content_html": "<p>" + "가" * 100 + "</p>"}, state)

    def test_related_box_prefers_same_subject(self):
        php = (ROOT / "wordpress" / "mu-plugins" / "first-lab-related.php").read_text(encoding="utf-8")
        self.assertIn("similar_text", php)   # 같은 주제 글끼리 먼저 연결 (주휴수당 뜻 ↔ 계산)

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
        # 외국 정부 (11/5 #567 ESTA 오탐)
        for u in ("https://esta.cbp.dhs.gov/faq", "https://www.ecfr.gov/x", "https://www.gov.uk/visa", "https://www.mofa.go.jp/a",
                  "https://www.immi.gov.au/a", "https://travel.state.gov/a", "https://france-visas.gouv.fr/a", "https://travel.gc.ca/a",
                  "https://home-affairs.ec.europa.eu/a"):
            self.assertTrue(is_official(u), u)
        for u in ("https://esta-visa.com/a", "https://gov.example.com/a", "https://mygov.net/a", "http://www.cbp.gov/a"):
            self.assertFalse(is_official(u), u)  # 정부 주소가 아님 / https 아님


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

    def test_recent_amendment_flag(self):
        """11/5 #512: 납부지연 가산세 조문이 2026.7.1 개정됐는데 개정 전 방식으로 계산 → 최근 개정 조문을 알려 준다."""
        import datetime as dt
        from bot import source_check as sc
        law = ("제47조의3(과소신고) ... <개정 2019. 12. 31.> 제47조의4(납부지연가산세) ① 납세의무자가 ... <개정 2018. 12. 31., 2025. 12. 23.> "
               "1. 납부일의 전날까지 제47조의5(원천징수) ... 부칙 제1조(시행일) 다만 제47조의4 및 제47조의5의 개정규정은 2026년 7월 1일부터 시행한다.")
        today = dt.date(2026, 11, 5)
        self.assertEqual(sc.recent_changes(law, "제47조의4", today), ["2025.12.23", "2026.07.01"])
        self.assertEqual(sc.recent_changes(law, "제47조의3", today), [])
        review = [{"claim": "납부지연가산세 13만 2,000원", "urls": ["https://www.law.go.kr/법령/국세기본법/제47조의4"], "verdict": "맞음", "action": "유지"}]
        got = sc.amended(review, fetcher=lambda u: law, today=today)
        self.assertEqual((got[0]["article"], got[0]["dates"][-1]), ("제47조의4", "2026.07.01"))

    def test_law_exceptions_and_absolute_sentences(self):
        """11/5 #535: 법 제46조②(하한)·제48조②(4년 상한)를 빼고 원칙만 씀 → 근거 조문의 예외 문장을 글쓴이에게 보여 준다."""
        from bot import source_check as sc
        law = ("제46조(구직급여일액) ①구직급여일액은 기초일액에 100분의 60을 곱한 금액으로 한다. "
               "②제1항제1호에 따라 산정된 구직급여일액이 최저구직급여일액보다 낮은 경우에는 최저구직급여일액을 그 수급자격자의 구직급여일액으로 한다. "
               "제47조(실업인정대상기간 중의 취업 등의 신고) ①수급자격자는 신고하여야 한다.")
        review = [{"claim": "금액은 평균임금의 60%", "urls": ["https://www.law.go.kr/법령/고용보험법/제46조"], "verdict": "맞음", "action": "유지"}]
        got = sc.law_exceptions(review, fetcher=lambda u: law)
        self.assertEqual(got[0]["article"], "제46조")
        self.assertTrue(any("최저구직급여일액보다 낮은 경우" in x for x in got[0]["exceptions"]))
        self.assertEqual(sc.absolute_sentences("<p>제출 서류는 보험증명서 하나예요. 기한은 3개월이에요.</p>"), ["제출 서류는 보험증명서 하나예요."])

    def test_article_from_claim_with_full_law_url(self):
        """법령 전체 주소(lsInfoR)여도 주장의 '법령명 제N조'로 그 조문의 예외를 찾는다."""
        from bot import source_check as sc
        law = "제48조(수급기간) ①12개월 내에 지급한다. ②신고한 경우에는 그 기간을 가산한 기간(4년을 넘을 때에는 4년)에 지급한다. 제49조(대기기간) ①7일."
        review = [{"claim": "수급기간 연장, 최대 4년 (고용보험법 제48조)", "urls": ["https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=284449&efYd=20260918"],
                   "verdict": "맞음", "action": "유지"}]
        got = sc.law_exceptions(review, fetcher=lambda u: law)
        self.assertEqual(got[0]["article"], "제48조")
        self.assertIn("4년을 넘을 때에는", got[0]["exceptions"][0])

    def test_headline_condition_missing(self):
        """11/5 #553: '세액공제 한도 74만 원' — 근거는 '총급여 3,300만 원 이하 74만 원'."""
        from bot import source_check as sc
        review = [{"claim": "한도: 총급여 3,300만 원 이하 74만 원 (소득세법 제59조)", "urls": ["https://www.law.go.kr/x"], "verdict": "맞음", "action": "유지"}]
        self.assertEqual(len(sc.headline_conditions(review, "근로소득세 계산법: 세액공제 한도 74만 원", "")), 1)
        self.assertEqual(sc.headline_conditions(review, "근로소득세 계산법: 세액공제 한도 최대 74만 원", ""), [])
        rate = [{"claim": "세율표: 1,400만 원 이하 6%", "urls": ["https://www.law.go.kr/x"], "verdict": "맞음", "action": "유지"}]
        self.assertEqual(sc.headline_conditions(rate, "세율 6~45%", ""), [])  # 범위로 쓴 것
        both = review + [{"claim": "세액공제 최고 한도 74만 원", "urls": ["https://www.law.go.kr/x"], "verdict": "맞음", "action": "유지"}]
        self.assertEqual(sc.headline_conditions(both, "세액공제 한도 74만 원", ""), [])  # 조건 없는 근거도 있음

    def test_short_real_page_is_readable(self):
        """11/5 #572: 한글 1,394자인 실제 안내 페이지를 껍데기로 잘못 셈 → 기준 400자."""
        from bot import source_check as sc
        self.assertLessEqual(sc.MIN_KOREAN, 1394)
        self.assertGreater(sc.MIN_KOREAN, 0)

    def test_law_notation(self):
        from bot import source_check as sc
        self.assertTrue(sc.has_number("세율은 1천분의 5", "0.5"))     # 0.5%
        self.assertTrue(sc.has_number("100분의 20을 곱한", "20"))
        self.assertFalse(sc.has_number("1천분의 50", "0.5"))
        self.assertTrue(sc.has_number("총급여액 7천만원 이하", "7000"))  # 7,000만 원
        self.assertTrue(sc.has_number("총급여액이 3천300만원 이하인 경우", "3300"))  # 11/5 #553
        self.assertTrue(sc.has_number("1억5천만원 이하", "15000"))
        self.assertFalse(sc.has_number("3천만원", "3300"))
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
        # 글쓰기 독립 검수자와 감시 외부 검수자가 같은 확인표를 쓴다 (11/5: 글쓰기 쪽 지시가 더 약해 외부 검수에서만 잡히던 오류)
        for f in ("daily-writer.md", "review-prompt.md"):
            self.assertIn("review-checklist.md", (ROOT / "automation" / f).read_text(encoding="utf-8"), f)
        self.assertIn("python3 -m bot.source_check", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))

    def test_no_absence_wording_in_fixes(self):
        """확인 못 한 것을 '없다·찾지 못했다'로 고치지 않는다 (11/4 #219 파인 대출 계산기)."""
        sys.path.insert(0, str(ROOT / "automation"))
        from verify_patches import superseded
        for i, p in enumerate(self.PATCHES):
            text = p.get("replace_paragraph", "") + p.get("insert_after", "") + p.get("set_title", "") + p.get("set_excerpt", "")
            if re.search(r"찾지 못했|못 찾았|확인되지 않았", text) and not superseded(p, self.PATCHES[i + 1:]):
                self.fail(f"patches.json {i}번(글 {p['post']}): '없음' 단정 문구 — 확인처 안내로 쓰기")


class StateMergeTest(unittest.TestCase):
    """11/5: 봇 실행 중 배포가 남긴 '직접 작성 글' 기록을 봇 저장이 덮어써 연속 작성이 멈춤 → 저장 때 합친다."""

    def test_save_keeps_log_written_by_other_process(self):
        from bot import state as st
        mine = {"plan": {}, "done": {}, "used_keywords": ["a"], "log": [{"time": "2026-11-05 09:30", "ok": True, "msg": "사진 채우기"}]}
        stored = {"used_keywords": ["a", "b"], "log": [{"time": "2026-11-05 08:56", "ok": True, "msg": "직접 작성 글: 증여세"},
                                                      {"time": "2026-11-05 08:46", "ok": True, "msg": "연속 작성 41번째"}]}
        written = []
        with mock.patch.object(st, "load_state", return_value=stored), \
             mock.patch.object(st, "wp_cli", side_effect=lambda *a, **k: written.append(k.get("stdin")) or ""), \
             mock.patch.object(st, "STATE_LOCK", "/nonexistent-dir/x.lock"):
            st.save_state(mine)
        saved = json.loads(written[-1])
        self.assertEqual([e["msg"] for e in saved["log"]], ["사진 채우기", "직접 작성 글: 증여세", "연속 작성 41번째"])
        self.assertEqual(saved["used_keywords"], ["a", "b"])


class LawTextTest(unittest.TestCase):
    """11/5 #537: 법령에 그대로 있는 내용이 검수자가 law.go.kr 을 못 열어 확인불가 → 조문 꺼내기 도구."""

    def test_prints_article_and_exceptions(self):
        import io
        from contextlib import redirect_stdout
        from bot import lawtext
        body = "제20조(살처분 명령) ④ 시장ㆍ군수ㆍ구청장은 광견병 예방주사를 맞지 아니한 개가 배회하면 억류할 수 있다. 다만, 소유자가 있으면 그러하지 아니하다. 제21조(도태) ① 권고한다."
        out = io.StringIO()
        with mock.patch.object(lawtext.law_api, "versions", return_value=[]), \
                mock.patch.object(lawtext, "law_body", return_value=body), redirect_stdout(out):  # API 없을 때 화면 읽기
            self.assertEqual(lawtext.main(["x", "가축전염병예방법", "제20조"]), 0)
        self.assertIn("배회하면 억류", out.getvalue())
        self.assertIn("[예외·단서]", out.getvalue())
        self.assertNotIn("도태", out.getvalue())

    def test_api_first_shows_current_version_and_upcoming_change(self):
        """법제처 API 가 되면 현행 판(시행일 기준) 조문과 앞으로 바뀌는 판을 보여 준다 — ID 로 받으면 미래 판이 오므로 판을 골라야 함."""
        import datetime as dt
        import io
        from contextlib import redirect_stdout
        from bot import lawtext
        today = dt.date.today()
        vers = [{"mst": "1", "date": today - dt.timedelta(days=30), "status": "현행"},
                {"mst": "2", "date": today + dt.timedelta(days=20), "status": "시행예정"}]
        texts = {"1": "제9조(산정) ① 비율을 곱한다. 다만, 경감한다.", "2": "제9조(산정) ① 비율(다섯째자리에서 반올림)을 곱한다."}
        out = io.StringIO()
        with mock.patch.object(lawtext.law_api, "versions", return_value=vers), \
                mock.patch.object(lawtext.law_api, "article", side_effect=lambda n, a, v: {"text": texts[v["mst"]], "changed": False, "date": v["date"]}), \
                redirect_stdout(out):
            self.assertEqual(lawtext.main(["x", "노인장기요양보험법", "제9조"]), 0)
        got = out.getvalue()
        self.assertIn("비율을 곱한다", got)          # 현행 판
        self.assertIn("[예외·단서] 다만, 경감한다.", got)
        self.assertIn("[시행 예정 개정]", got)       # 미래 판은 따로 표시
        self.assertIn("반올림", got)


class SchemaPluginTest(unittest.TestCase):
    """AEO·GEO (11/5 운영자 승인): 글 화면에 Article 구조화 데이터와 업데이트 날짜. 빠지면 검색·AI 가 작성자·시점·근거를 못 읽는다."""

    SRC = (ROOT / "wordpress" / "mu-plugins" / "first-lab-schema.php").read_text(encoding="utf-8")

    def test_article_fields(self):
        for field in ("'Article'", "'datePublished'", "'dateModified'", "'author'", "'publisher'", "'image'", "'citation'", "'inLanguage'"):
            self.assertIn(field, self.SRC)

    def test_citation_uses_official_links_section(self):
        # 글 끝 공식 링크 제목이 바뀌면 인용 출처가 비므로 같은 문구를 쓰는지 확인
        from bot.sources import HEADING
        self.assertIn(f"'{HEADING}'", self.SRC)
        self.assertIn("html_entity_decode", self.SRC)  # 11/5 배포 확인: citation 주소에 &amp; 가 그대로 들어감


class CalculatorLinksTest(unittest.TestCase):
    """계산기 결과의 관련 글 링크(data.links): 공개된 글·페이지 주소만 화면에 넣는다.
    11/5 혜택 찾기 계산기: 관련 글(에너지바우처·실업급여 조건·내일배움카드)이 대기열엔 있어도 사이트엔 아직 비공개라 그대로 넣으면 404."""

    PLUGIN = ROOT / "wordpress" / "mu-plugins" / "first-lab-calculators.php"
    THEME = ROOT / "wordpress" / "themes" / "first-lab"
    STUB = r"""<?php
define('ABSPATH', '/');
define('OBJECT', 'OBJECT');
function add_shortcode(...$a) {}
function get_stylesheet_directory() { return getenv('FL_THEME'); }
function get_stylesheet_directory_uri() { return ''; }
function get_page_by_path($slug, $output, $types) {
    $posts = array('에너지바우처신청' => 'publish', '내일배움카드자격' => 'draft');
    return isset($posts[$slug]) ? (object) array('st' => $posts[$slug]) : null;
}
function get_post_status($post) { return $post->st; }
function wp_json_encode($data, $flags) { return json_encode($data, $flags); }
require getenv('FL_PLUGIN');
echo firstlab_calc_rates(getenv('FL_DATA'));
"""

    def rates(self, rel):
        import os
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("php"):
            self.skipTest("php 없음")
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(self.STUB)
        try:
            env = {**os.environ, "FL_THEME": str(self.THEME), "FL_PLUGIN": str(self.PLUGIN), "FL_DATA": rel}
            return subprocess.run(["php", f.name], capture_output=True, text=True, env=env, check=True).stdout
        finally:
            os.unlink(f.name)

    def test_unpublished_links_removed(self):
        data = json.loads(self.rates("/data/benefit-finder.json"))
        self.assertEqual(data["links"], {"energy_voucher": "/에너지바우처신청/"})  # 초안·없는 글 주소는 빠짐
        self.assertEqual(data["basis_date"], "2026-11-05")  # 나머지 값은 그대로

    def test_calculator_without_links_unchanged(self):
        raw = (self.THEME / "calculators" / "data" / "vat.json").read_text(encoding="utf-8")
        self.assertEqual(self.rates("/data/vat.json"), raw)


class ApplyReviewTest(unittest.TestCase):
    """검수 반영: 패치가 하나라도 검사에 걸리면 아무것도 저장·기록하지 않는다 (11/5 #542: 패치 실패에도 검수 완료가 기록됨)."""

    def test_bad_patch_blocks_everything(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        post = {"title": "t", "excerpt": "e", "content_html": "<p>가산점이 있고 1점이에요.</p>"}
        bad = [{"type": "outdated", "why": "x", "find": "가산점이 있고", "replace_paragraph": "가산점이 있고 바뀌어요", "tag": "p"}]
        self.assertTrue(any("그대로 있음" in e for e in apply_review.check(post, bad)))
        unknown = [{"type": "없는유형", "why": "x", "find": "1점이에요", "replace_paragraph": "1점", "tag": "p"}]
        self.assertTrue(any("유형" in e for e in apply_review.check(post, unknown)))
        good = [{"type": "outdated", "why": "x", "find": "1점이에요", "replace_paragraph": "1점이고 바뀌어요", "tag": "p",
                 "prevention": "review_only:개정 시행일은 근거 페이지마다 달라 코드로 판단 불가"}]
        self.assertEqual(apply_review.check(post, good), [])

    def test_softening_keeps_verified_numbers(self):
        """조건 추가·완화가 공식 페이지에서 확인된 숫자를 지우면 막음 (11/5 #673 200원·230원)."""
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        post = {"title": "t", "excerpt": "e", "content_html": "<ul><li>건강보험 200원, 연금 230원이 줄어요.</li></ul>",
                "source_check": {"found": [{"claim": "200원·230원 감액", "numbers": ["200", "230"], "urls": ["https://x.or.kr"]}]}}
        soft = [{"type": "unverified", "why": "x", "find": "연금 230원이 줄어요.", "replace_paragraph": "가입자마다 달라요. 공단에 확인하세요.", "tag": "li",
                 "prevention": "check:numbers_match"}]
        self.assertTrue(any("200, 230" in e for e in apply_review.check(post, soft)))
        keep = [dict(soft[0], replace_paragraph="건강보험 200원, 연금 230원이 줄어요(지역가입자 기준). 사업장은 공단에 확인하세요.")]
        self.assertEqual(apply_review.check(post, keep), [])
        fix = [dict(soft[0], type="wrong_fact", replace_paragraph="건강보험 300원이 줄어요.", prevention="review_only:공식 금액이 무엇인지는 코드가 판단 불가")]   # 숫자가 틀렸다고 고치는 유형은 허용
        self.assertEqual(apply_review.check(post, fix), [])


class PreventionRequiredTest(unittest.TestCase):
    """고칠 때마다 재발 방지를 같이 (11/5 운영자 '하나라도 수정했으면 다음부터 그런 오류가 안 생기게 해야지')."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        self.ar = apply_review
        self.post = {"title": "백일해 예방접종", "excerpt": "e",   # #748 사례
                     "content_html": "<p>반대로 성인이 맞는 Tdap은 국가 지원 대상이 아니라 병원마다 금액이 달라요.</p>"
                                     "<h2>지원</h2><p>만 13세 이상은 백일해가 유행할 때 임시예방접종 대상이면 국가 지원을 받을 수 있어요.</p>"}
        self.base = {"type": "missing_condition", "why": "x", "find": "국가 지원 대상이 아니라", "tag": "p",
                     "replace_paragraph": "성인 Tdap은 유행 때 임시예방접종 대상인 경우가 아니면 국가 지원이 없어요."}

    def errs(self, **kw):
        return self.ar.check(self.post, [dict(self.base, **kw)])

    def test_missing_prevention_blocked(self):
        self.assertTrue(any("prevention 없음" in e for e in self.errs()))

    def test_unknown_check_blocked(self):
        self.assertTrue(any("못 찾음" in e for e in self.errs(prevention="check:없는검사")))
        self.assertTrue(any("못 찾음" in e for e in self.errs(prevention="check:a")))   # 아무 글자나 이름으로 못 씀

    def test_named_gate_must_fire_on_original(self):
        self.assertEqual(self.errs(prevention="check:lead_vs_body"), [])
        self.post["content_html"] = "<p>반대로 성인이 맞는 Tdap은 국가 지원 대상이 아니라 병원마다 금액이 달라요.</p>"
        self.assertTrue(any("안 걸림" in e for e in self.errs(prevention="check:lead_vs_body")))

    def test_review_only_only_for_uncodeable_types(self):
        self.assertEqual(self.errs(prevention="review_only:어떤 예외가 빠졌는지는 법령 전체를 읽어야 앎"), [])
        self.assertTrue(any("너무 짧음" in e for e in self.errs(prevention="review_only:몰라")))
        self.assertTrue(any("코드 검사가 있는 유형" in e for e in self.errs(type="headline", prevention="review_only:제목이 본문과 다름을 고침")))


class IndexNowTest(unittest.TestCase):
    """글이 공개되는 순간 검색엔진에 알린다 (11/5 운영자 승인)."""

    def test_payload_and_publish_ping(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot import indexnow, schedule
        p = indexnow.payload(["https://first-lab.kr/a/"])
        self.assertEqual((p["host"], p["keyLocation"]), ("first-lab.kr", f"https://first-lab.kr/{indexnow.KEY}.txt"))
        self.assertRegex(indexnow.KEY, r"^[0-9a-f]{32}$")

        def wp(*args):
            if args[:2] == ("post", "list"):
                return "80\n"
            return {"--field=post_title": "글", "--field=post_name": "%ec%9d%b4%ec%82%ac"}.get(args[-1], "")
        with mock.patch.object(schedule, "wp_cli", side_effect=wp), \
             mock.patch.object(indexnow, "notify", return_value="IndexNow 알림 200 (1개)") as ping:
            msg = schedule.publish_next(datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")))
        ping.assert_called_once_with(["https://first-lab.kr/%ec%9d%b4%ec%82%ac/"])
        self.assertIn("IndexNow", msg)

    def test_key_file_deployed(self):
        self.assertIn("INDEXNOW_KEY", (ROOT / "server" / "wp-setup.sh").read_text(encoding="utf-8"))

    def test_editorial_policy_linked(self):
        self.assertTrue((ROOT / "wordpress" / "pages" / "editorial-policy.html").read_text(encoding="utf-8").startswith("<!-- title: 검수 원칙 -->"))
        self.assertIn("/editorial-policy/", (ROOT / "wordpress" / "themes" / "first-lab" / "parts" / "footer.html").read_text(encoding="utf-8"))
        self.assertIn("/editorial-policy/", (ROOT / "wordpress" / "mu-plugins" / "first-lab-author-box.php").read_text(encoding="utf-8"))


class FreshnessTest(unittest.TestCase):
    """공개 직전 확인 (11/5 운영자: 초안을 쓰고 나중에 공개하면 그사이 법이 바뀔 수 있음)."""

    LAW = ("제47조의4(납부지연가산세) ① 납부일의 전날까지 계산한다. <개정 2025. 12. 23.> 제47조의5(원천징수) ① ... "
           "부칙 제1조(시행일) 다만 제47조의4 및 제47조의5의 개정규정은 2026년 7월 1일부터 시행한다.")

    def test_law_changed_after_draft_is_held(self):
        import datetime as dt
        from bot import freshness
        record = {"laws": [["https://www.law.go.kr/법령/국세기본법", "제47조의4"]], "found": []}
        got = freshness.problems(record, [], dt.date(2026, 6, 1), dt.date(2026, 11, 5), fetcher=lambda u: self.LAW)
        self.assertEqual(len(got), 1)
        self.assertIn("2026.07.01", got[0])
        # 개정 뒤에 쓴 글은 통과
        self.assertEqual(freshness.problems(record, [], dt.date(2026, 7, 2), dt.date(2026, 11, 5), fetcher=lambda u: self.LAW), [])

    def test_old_draft_without_article_checks_whole_law(self):
        """조문 번호 없는 예전 초안: 근거 법령이 검수 뒤 시행됐으면 다시 확인 (11/5 운영자: 공개 직전 확인)."""
        import datetime as dt
        from bot import freshness
        review = [{"claim": "납부지연가산세 1일 0.022%", "urls": ["https://www.law.go.kr/법령/국세기본법"], "verdict": "맞음", "action": "유지"}]
        law = "부칙 제1조(시행일) 이 법은 2026년 7월 1일부터 시행한다."
        self.assertEqual(len(freshness.problems({}, review, dt.date(2026, 6, 1), dt.date(2026, 11, 5), fetcher=lambda u: law)), 1)
        self.assertEqual(freshness.problems({}, review, dt.date(2026, 7, 2), dt.date(2026, 11, 5), fetcher=lambda u: law), [])

    def test_number_disappeared_from_official_page(self):
        import datetime as dt
        from bot import freshness
        record = {"laws": [], "found": [{"claim": "응시료 28,000원", "numbers": ["28000"], "urls": ["https://www.q-net.or.kr/a"]}]}
        now_page = "응시수수료 30,000원"
        self.assertEqual(len(freshness.problems(record, [], dt.date(2026, 11, 1), dt.date(2026, 11, 5), fetcher=lambda u: now_page)), 1)
        self.assertEqual(freshness.problems(record, [], dt.date(2026, 11, 1), dt.date(2026, 11, 5), fetcher=lambda u: None), [])  # 못 열면 막지 않음

    def test_publish_holds_changed_draft_and_publishes_next(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot import freshness, schedule
        calls = []

        def wp(*args):
            calls.append(args)
            if args[:2] == ("post", "list"):
                return "80\n85\n"
            return "글" if "--field=post_title" in args else ""
        with mock.patch.object(schedule, "wp_cli", side_effect=wp), \
             mock.patch.object(freshness, "check_post", side_effect=lambda pid, today: ["국세기본법 제47조의4 2026.07.01 개정·시행"] if pid == 80 else []):
            msg = schedule.publish_next(datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertIn(("post", "meta", "update", "80", "_firstlab_hold"), [c[:5] for c in calls])
        self.assertEqual(next(c for c in calls if c[:2] == ("post", "update"))[2], "85")
        self.assertIn("보류", msg)


class GoldFirstTest(unittest.TestCase):
    """11/5 운영자 지시: 황금 키워드는 점수 높은 것부터 쓰고 공개한다."""

    def test_publish_order_gold_first_but_off_season_last(self):
        import json as _json
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot import freshness, naver_hub, schedule
        calls = []
        rows = [{"ID": 80, "post_name": "%EC%BA%A0%ED%95%91%EC%A4%80%EB%B9%84%EB%AC%BC", "post_title": "캠핑"},      # 캠핑준비물
                {"ID": 85, "post_name": "%EC%97%B0%EB%A7%90%EC%A0%95%EC%82%B0", "post_title": "연말정산"},          # 연말정산
                {"ID": 90, "post_name": "%ED%95%B4%EC%88%98%EC%9A%95%EC%9E%A5", "post_title": "해수욕장 개장"}]    # 해수욕장 (여름, 11월엔 지남)

        def wp(*args):
            calls.append(args)
            if args[:2] == ("post", "list") and "--format=json" in args:
                return _json.dumps(rows)
            if args[:2] == ("post", "list"):
                return "80\n85\n90\n"
            return "글" if "--field=post_title" in args else ""
        prio = {80: 0.5, 85: 6.0, 90: 9.0}
        with mock.patch.object(schedule, "wp_cli", side_effect=wp), \
             mock.patch.object(naver_hub, "publish_priority", return_value=prio), \
             mock.patch.object(schedule.seasons, "rank", side_effect=lambda text, today: schedule.seasons.OFF if "해수욕장" in text else schedule.seasons.NONE), \
             mock.patch.object(freshness, "check_post", return_value=[]):
            schedule.publish_next(datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertEqual(next(c for c in calls if c[:2] == ("post", "update"))[2], "85")  # 황금 점수 높은 글 먼저, 시즌 지난 글은 점수가 높아도 뒤

    def test_refresh_queue_orders_waiting_drafts(self):
        """대기 중인 글도 키워드를 다시 조회해 줄 세움: 창고에 없는 키워드는 검색량을 채우고 '쓴 키워드'로 표시."""
        import datetime as dt
        import sqlite3
        from types import SimpleNamespace as NS
        from bot import keyword_db, naver_hub as h
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(keyword_db.SCHEMA)
        keyword_db._migrate(conn)
        conn.execute("INSERT INTO keywords(keyword, norm, total, status, first_seen, last_checked) VALUES ('퇴직금계산기','퇴직금계산기',9000,'used','2026-11-01','2026-11-01')")

        class Ads:
            def keyword_stats(self, hints):
                return [NS(keyword="에너지바우처신청", total=4000, ad_competition="", ad_clicks=0.0),
                        NS(keyword="에너지바우처", total=90000, ad_competition="", ad_clicks=0.0)]  # 연관어는 넣지 않음
        series = {f"{y}-{m:02d}": (90.0 if m in (11, 12) else 10.0) for y in (2025, 2026) for m in range(1, 13)}
        with mock.patch.object(h, "blog_totals", side_effect=lambda w, k, t, save=None: {"퇴직금계산기": {"docs": 400000, "hits": 29, "old": 0},
                                                                                         "에너지바우처신청": {"docs": 800, "hits": 1, "old": 0}}), \
                mock.patch.object(h, "monthly_trend", return_value={"에너지바우처신청": series}):
            msg = h.refresh_queue(conn, dt.date(2026, 11, 5), keys=("i", "k"), drafts={80: "퇴직금계산기", 85: "에너지바우처신청"}, ads=Ads())
        row = conn.execute("SELECT status, total FROM keywords WHERE norm='에너지바우처신청'").fetchone()
        self.assertEqual((row["status"], row["total"]), ("used", 4000))      # 새 글 후보로 다시 안 뜸
        self.assertIsNone(conn.execute("SELECT 1 FROM keywords WHERE norm='에너지바우처'").fetchone())
        self.assertIn("공개 순서 상위: 에너지바우처신청", msg)               # 황금(0.2)·성수기(11월) → 먼저
        prio = h.publish_priority({80: "퇴직금계산기", 85: "에너지바우처신청"}, 11, conn=conn)
        self.assertGreater(prio[85], prio[80])

    def test_candidates_gold_group_first(self):
        import sqlite3
        from bot import keyword_db, naver_hub, select_keyword
        from bot.settings import BotSettings
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(keyword_db.SCHEMA)
        keyword_db._migrate(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS gsc_signals (norm TEXT PRIMARY KEY, kind TEXT, note TEXT)")
        for kw, total in (("퇴직금 계산기", 9000), ("육아휴직 급여 신청", 1200)):
            conn.execute("INSERT INTO keywords(keyword, norm, total, first_seen, last_checked) VALUES (?,?,?,?,?)",
                         (kw, keyword_db.norm(kw), total, "2026-11-05", "2026-11-05"))
        naver_hub.load(conn)
        conn.execute("INSERT INTO naver_signals(norm, docs, hits, docs_checked) VALUES ('육아휴직급여신청', 800, 1, '2026-11-05')")   # 황금
        conn.execute("INSERT INTO naver_signals(norm, docs, hits, docs_checked) VALUES ('퇴직금계산기', 400000, 29, '2026-11-05')")  # 포화
        got = select_keyword.ranked_candidates(conn, BotSettings(min_volume=500, max_volume=30000), limit=5)
        self.assertEqual(got[0]["keyword"], "육아휴직 급여 신청")
        self.assertTrue(got[0]["gold"])
        self.assertFalse(got[1]["gold"])


class TopicDirectionTest(unittest.TestCase):
    """11/5 운영자 결정: 계산기·돈 주제 우선, 같은 뜻 다른 표기는 이미 쓴 주제로."""

    def test_synonym_duplicates_and_weights(self):
        from difflib import SequenceMatcher
        from bot.select_keyword import SIMILAR_USED, _core, topic_weight, unify
        same = lambda a, b: SequenceMatcher(None, _core(unify(a)), _core(unify(b))).ratio() >= SIMILAR_USED  # noqa: E731
        self.assertTrue(same("부동산양도세계산기", "부동산양도소득세계산기"))
        self.assertTrue(same("나의신용점수조회", "내신용점수조회"))
        self.assertTrue(same("실업급여수급조건", "고용보험실업급여조건"))
        self.assertFalse(same("실업급여조건6개월", "고용보험실업급여조건"))  # 세부 질문은 따로 쓸 수 있음
        self.assertGreater(topic_weight("주식평단가계산기"), topic_weight("실업급여신청"))
        self.assertGreater(topic_weight("실업급여신청"), topic_weight("냉장고정리방법"))
        self.assertLess(topic_weight("상하이여행준비물"), 0)


class SeasonTest(unittest.TestCase):
    """계절 공개 순서 (11/5 운영자 승인): 시즌 중인 글 먼저, 시즌 지난 글은 뒤로."""

    def test_rank(self):
        import datetime as dt
        from bot import seasons
        self.assertEqual(seasons.rank("종합소득세신고기간 종합소득세 신고기간", dt.date(2026, 4, 10)), seasons.IN)
        self.assertEqual(seasons.rank("종합소득세신고기간", dt.date(2026, 11, 5)), seasons.OFF)
        self.assertEqual(seasons.rank("부가가치세계산기", dt.date(2026, 1, 10)), seasons.IN)  # 연도를 넘는 구간
        self.assertEqual(seasons.rank("연말정산 서류", dt.date(2026, 12, 1)), seasons.IN)
        self.assertEqual(seasons.rank("실업급여신청절차", dt.date(2026, 11, 5)), seasons.NONE)

    def test_publish_prefers_in_season_and_defers_off_season(self):
        import json as _json
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot import freshness, schedule
        calls = []
        rows = [{"ID": 80, "post_name": "종합소득세신고기간", "post_title": "종합소득세 신고기간"},
                {"ID": 85, "post_name": "실업급여신청절차", "post_title": "실업급여"},
                {"ID": 90, "post_name": "에너지바우처신청", "post_title": "에너지바우처"}]

        def wp(*args):
            calls.append(args)
            if args[:2] == ("post", "list"):
                return _json.dumps(rows) if "--format=json" in args else "80\n85\n90\n"
            return "글" if "--field=post_title" in args else ""
        with mock.patch.object(schedule, "wp_cli", side_effect=wp), mock.patch.object(freshness, "check_post", return_value=[]):
            schedule.publish_next(datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")))
        # 11월: 에너지바우처(시즌 중) 먼저 — 종합소득세(시즌 지남)는 가장 오래됐어도 뒤로
        self.assertEqual(next(c for c in calls if c[:2] == ("post", "update"))[2], "90")


class LawWatchTest(unittest.TestCase):
    """법령 변경 감시 (11/5 운영자 승인): 곧 시행될 개정은 글감으로, 막 시행된 개정은 기존 글 재검증."""

    LAW = ("제46조(구직급여일액) ① ... 부칙 <법률 제21473호, 2026. 9. 2.> 제1조(시행일) 이 법은 2026년 9월 18일부터 시행한다. "
           "다만, 제46조 및 제48조의 개정규정은 2027년 1월 1일부터 시행한다.")

    def test_scan_upcoming_and_just_effective(self):
        import datetime as dt
        from bot import law_watch
        laws = {"https://www.law.go.kr/법령/고용보험법": {"실업급여신청절차"}}
        upcoming, just = law_watch.scan(laws, dt.date(2026, 10, 1), fetcher=lambda u: self.LAW)
        self.assertEqual([(u["date"], u["articles"]) for u in upcoming], [("2027-01-01", ["제46조", "제48조"])])
        self.assertEqual(just[0]["date"], "2026-09-18")
        self.assertEqual(upcoming[0]["key"], "고용보험법|2027-01-01")
        self.assertEqual(upcoming[0]["posts"], ["실업급여신청절차"])

    def test_names_and_dedupe_across_urls(self):
        import datetime as dt
        from bot import law_watch
        text = ("판례 연혁 고용보험법 [시행 2026. 9. 18.] ... 부칙 제1조(시행일) 이 법은 2027년 1월 1일부터 시행한다. "
                "다만, 제46조의 개정규정은 2027년 3월 1일부터 시행한다.")
        laws = {"https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=1": {"a"}, "https://www.law.go.kr/법령/고용보험법": {"b"}}
        upcoming, _ = law_watch.scan(laws, dt.date(2026, 11, 5), fetcher=lambda u: text)
        self.assertEqual([(u["law"], u["date"], u["articles"]) for u in upcoming],
                         [("고용보험법", "2027-01-01", []), ("고용보험법", "2027-03-01", ["제46조"])])
        self.assertEqual(upcoming[0]["posts"], ["a", "b"])  # 두 주소의 글을 합침
        self.assertEqual(law_watch.title_of(" [시행 2026. 1. 1.]"), "")  # 이름 자리가 빈 페이지
        self.assertEqual(law_watch.title_of("판례 연혁 법인세법 시행령 [시행 2026. 1. 1.]"), "법인세법 시행령")

    def test_law_key_dedupes(self):
        from bot.law_watch import law_key
        self.assertEqual(law_key("https://www.law.go.kr/법령/고용보험법/제46조"), law_key("https://www.law.go.kr/법령/고용보험법"))
        self.assertEqual(law_key("https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=284449&efYd=20260918"),
                         "https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=284449&efYd=20260918")
        self.assertIsNone(law_key("https://www.law.go.kr/LSW/flDownload.do?flSeq=1"))  # 별표 PDF

    def test_proposals_and_scheduled_versions(self):
        import datetime as dt
        from bot import law_watch
        row = ('<a href="/gcom/ogLmPp/89364" title="x">지방세법 시행령 일부개정령안 입법예고</a> <td data-th="소관부처 (법령종류)">'
               '<p>행정안전부</p></td><td data-th="입법의견 접수기간"><p>2026. 11. 5.</p><p>~2026. 12. 15.</p></td>'
               '<a href="/gcom/ogLmPp/89372" title="y">지방세기본법 시행령 일부개정령안 입법예고</a> <td data-th="소관부처 (법령종류)">'
               '<p>행정안전부</p></td><td data-th="입법의견 접수기간"><p>2026. 11. 5.</p><p>~2026. 12. 15.</p></td>')
        got = law_watch.proposals({"지방세법"}, fetch_page=lambda u: row if "pageIndex=1" in u else "")
        self.assertEqual([g["title"] for g in got], ["지방세법 시행령 일부개정령안 입법예고"])  # 지방세기본법은 다른 법
        self.assertEqual(got[0]["period"], "2026. 11. 5. ~ 2026. 12. 15.")

    def test_api_changes_compares_cited_articles(self):
        """법제처 API 판 비교: 근거 조문이 실제로 바뀌는 판만 그 조문·글을 붙이고, 막 시행된 판은 바뀐 조문 글만 재검증."""
        import datetime as dt
        from bot import law_api, law_watch
        d = dt.date
        vers = [{"mst": "1", "date": d(2026, 8, 20), "status": "연혁"}, {"mst": "2", "date": d(2026, 10, 20), "status": "현행"},
                {"mst": "3", "date": d(2026, 11, 27), "status": "시행예정"}, {"mst": "4", "date": d(2027, 1, 1), "status": "시행예정"}]
        texts = {("제9조", "1"): "옛 9조", ("제9조", "2"): "새 9조 <개정 2026.5.26>", ("제9조", "3"): "새 9조",
                 ("제9조", "4"): "더 새 9조", ("제3조", "1"): "3조", ("제3조", "2"): "3조", ("제3조", "3"): "3조", ("제3조", "4"): "3조"}
        fake_article = lambda name, art, v: {"text": texts[(art, v["mst"])], "changed": False, "date": v["date"]}
        cited = {"/법령/노인장기요양보험법": {"제9조": {"월급계산기"}, "제3조": {"요양등급"}}}
        with mock.patch.object(law_api, "versions", return_value=vers), mock.patch.object(law_api, "article", side_effect=fake_article):
            up, just = law_watch.api_changes(cited, {"/법령/노인장기요양보험법": "노인장기요양보험법"}, d(2026, 11, 5))
        self.assertEqual([(u["date"], u["articles"], u["posts"]) for u in up],
                         [("2026-11-27", [], []), ("2027-01-01", ["제9조"], ["월급계산기"])])  # 11/27 은 '<개정>' 표시만 달라 같은 조문
        self.assertEqual([(j["date"], j["articles"], j["posts"]) for j in just], [("2026-10-20", ["제9조"], ["월급계산기"])])  # 3조 글은 재검증 안 함
        merged = law_watch.merge([{"key": "노인장기요양보험법|2027-01-01", "date": "2027-01-01", "articles": ["부칙"]}], up)
        self.assertEqual([m["articles"] for m in merged], [[], ["제9조"]])  # 같은 판이면 API 결과로

    def test_refresh_picks_law_changed_post_first(self):
        from bot import refresh
        posts = [{"id": 1, "title": "a", "url": "u1", "date": "2026-11-01", "refreshed": "", "has_number": True, "law_changed": ""},
                 {"id": 2, "title": "b", "url": "u2", "date": "2026-11-01", "refreshed": "2026-10-01", "has_number": True,
                  "law_changed": "2026-11-01 고용보험법"}]
        with mock.patch.object(refresh, "wp_cli", return_value=json.dumps(posts)), \
             mock.patch.object(refresh, "_history", return_value=(None, None)), mock.patch.object(refresh, "siblings", return_value=[]):
            picks = refresh.pick_targets(sqlite3.connect(":memory:"), 1)
        self.assertEqual((picks[0][0]["id"], picks[0][1]), (2, "recheck"))

    def test_done_list_survives_concurrent_save(self):
        from bot import state as st
        mine = {"plan": {}, "done": {}, "used_keywords": [], "log": [], "law_changes_done": []}
        stored = {"law_changes_done": ["고용보험법|2027-01-01"]}
        written = []
        with mock.patch.object(st, "load_state", return_value=stored), \
             mock.patch.object(st, "wp_cli", side_effect=lambda *a, **k: written.append(k.get("stdin")) or ""), \
             mock.patch.object(st, "STATE_LOCK", "/nonexistent-dir/x.lock"):
            st.save_state(mine)
        self.assertEqual(json.loads(written[-1])["law_changes_done"], ["고용보험법|2027-01-01"])

    def test_writer_gets_law_changes(self):
        self.assertIn("law_changes", (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8"))
        self.assertIn("law_changes", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))


class DeploySpeedTest(unittest.TestCase):
    """11/5: 배포가 10분 제한을 넘겨 계속 실패 (글마다 wp-cli 호출). 보정·사진 정리는 한 번에 읽은 글로 처리."""

    def test_apply_patches_uses_bulk_without_per_post_reads(self):
        from bot import fix_posts
        bulk = {"1": {"content": "<p>옛 문장</p>", "title": "t", "excerpt": "e", "name": "n", "status": "draft", "meta": {}}}
        calls = []
        patches = [{"post": 1, "why": "x", "find": "옛 문장", "replace_paragraph": "새 문장", "tag": "p"},
                   {"post": 1, "why": "y", "set_title": "새 제목", "from_title": "t"}]
        with mock.patch.object(fix_posts, "PATCHES") as path, mock.patch.object(fix_posts, "wp_cli", side_effect=lambda *a: calls.append(a) or ""):
            path.exists.return_value = True
            path.read_text.return_value = json.dumps(patches, ensure_ascii=False)
            fix_posts.apply_patches(bulk)
        self.assertFalse([c for c in calls if c[:2] == ("post", "get")])  # 글마다 읽지 않음
        self.assertEqual([c[:3] for c in calls], [("post", "update", "1"), ("post", "update", "1")])
        self.assertIn("새 문장", bulk["1"]["content"])
        self.assertEqual(bulk["1"]["title"], "새 제목")

    def test_deploy_timeout_raised(self):
        self.assertIn("command_timeout: 30m", (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8"))

class LawApiTest(unittest.TestCase):
    """법령 API: 서버는 신청값으로 직접, 다른 곳은 사이트 통로(토큰), 둘 다 없으면 None (화면 읽기로)."""

    def test_proxy_with_token_and_none_without(self):
        from bot import law_api
        with mock.patch.object(law_api, "_server_oc", return_value=""), \
                mock.patch.dict("os.environ", {"FIRSTLAB_API_TOKEN": "t"}), \
                mock.patch.object(law_api, "_get", return_value=json.dumps({"status": 200, "body": "<LawSearch/>"})) as get:
            self.assertEqual(law_api.fetch("search", "law", query="고용보험법"), "<LawSearch/>")
            url, headers = get.call_args[0]
            self.assertTrue(url.startswith(law_api.PROXY) and "kind=search" in url)
            self.assertEqual(headers, {"X-Firstlab-Token": "t"})
        with mock.patch.object(law_api, "_server_oc", return_value=""), mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(law_api.fetch("search", "law", query="x"))
        with mock.patch.object(law_api, "_server_oc", return_value="oc1"), \
                mock.patch.object(law_api, "_get", side_effect=OSError("끊김")):
            self.assertIsNone(law_api.fetch("service", "law", MST="1"))  # 실패해도 예외 대신 None
        err = '<?xml version="1.0"?>\n<Response><result>사용자 정보 검증에 실패하였습니다.</result></Response>'
        with mock.patch.object(law_api, "_server_oc", return_value="oc1"), mock.patch.object(law_api, "_get", return_value=err):
            self.assertIsNone(law_api.fetch("search", "law", query="x"))  # 200 으로 온 오류 응답도 None

    def test_site_route_whitelist(self):
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        code = ("define('ABSPATH',1); function add_action(){} require '" + str(ROOT / "wordpress/mu-plugins/first-lab-api.php") + "';"
                "echo json_encode([firstlab_law_url(['kind'=>'service','target'=>'law','MST'=>'9','JO'=>'004000','evil'=>'x'],'OC1'),"
                "firstlab_law_url(['kind'=>'search','target'=>'../etc'],'OC1'), firstlab_law_url(['kind'=>'x'],'OC1')]);")
        ok, bad_target, bad_kind = json.loads(subprocess.run(["php", "-r", code], capture_output=True, text=True, check=True).stdout)
        self.assertTrue(ok.startswith("https://www.law.go.kr/DRF/lawService.do?OC=OC1&target=law&type=XML"))
        self.assertIn("JO=004000", ok)
        self.assertNotIn("evil", ok)
        self.assertIsNone(bad_target)
        self.assertIsNone(bad_kind)


class NestedParagraphTest(unittest.TestCase):
    def test_patch_strips_outer_tag_and_repairs_nested(self):
        """11/5 #578: 고친 문구에 '<p>…</p>' 까지 넣어 '<p><p>…</p></p>' 이 됨 → 적용 때 벗기고, 이미 생긴 것은 바로잡고, 반영 도구가 거부."""
        from bot.fix_posts import patch_content, unnest
        content = "<p>앞 문장.</p>\n\n<p>옛 문장이 여기 있어요.</p>"
        got = patch_content(content, {"find": "옛 문장", "replace_paragraph": "<p>새 문장이에요.</p>"})
        self.assertIn("<p>새 문장이에요.</p>", got)
        self.assertNotIn("<p><p>", got)
        self.assertEqual(unnest("<h2>x</h2>\n<p><p>버팀목 대출은…</p></p>\n<p>다음</p>"), "<h2>x</h2>\n<p>버팀목 대출은…</p>\n<p>다음</p>")
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        post = {"content_html": "<p>옛 문장이 여기 있어요.</p>", "title": "t", "excerpt": "e"}
        errs = apply_review.check(post, [{"type": "wrong_fact", "why": "w", "find": "옛 문장", "replace_paragraph": "<p>새 문장</p>"}])
        self.assertTrue(any("안쪽만" in e for e in errs))


class LawApiVersionsTest(unittest.TestCase):
    def test_versions_one_per_date_latest_promulgation(self):
        """같은 시행일 판이 여럿이면 마지막 공포 판 하나만 (실제 소득세법 2027.1.1 판이 6개로 나와 감시 목록이 중복됨)."""
        from bot import law_api
        xml = ("<LawSearch>"
               "<law id='1'><법령일련번호>100</법령일련번호><법령명한글><![CDATA[소득세법]]></법령명한글><시행일자>20270101</시행일자><현행연혁코드>시행예정</현행연혁코드></law>"
               "<law id='2'><법령일련번호>120</법령일련번호><법령명한글><![CDATA[소득세법]]></법령명한글><시행일자>20270101</시행일자><현행연혁코드>시행예정</현행연혁코드></law>"
               "<law id='3'><법령일련번호>90</법령일련번호><법령명한글><![CDATA[소득세법]]></법령명한글><시행일자>20260701</시행일자><현행연혁코드>현행</현행연혁코드></law>"
               "<law id='4'><법령일련번호>95</법령일련번호><법령명한글><![CDATA[소득세법 시행령]]></법령명한글><시행일자>20260701</시행일자><현행연혁코드>현행</현행연혁코드></law>"
               "</LawSearch>")
        with mock.patch.object(law_api, "fetch", return_value=xml):
            got = law_api.versions("소득세법")
        self.assertEqual([(v["mst"], str(v["date"])) for v in got], [("90", "2026-07-01"), ("120", "2027-01-01")])
        self.assertEqual(law_api.jo_code("제167조의10"), "016710")


class NaverHubTest(unittest.TestCase):
    """네이버 API HUB (11/5 운영자 승인: 블로그 문서 수·검색어 트렌드만, 종류별 하루 상한) — 황금 키워드·성수기."""

    def test_season_and_score(self):
        from bot import naver_hub as h
        series = {f"{y}-{m:02d}": (100.0 if m in (1, 2) else 50.0 if m == 3 else 10.0) for y in (2025, 2026) for m in range(1, 13)}
        peak, low = h.season(series)
        self.assertEqual(peak, [1, 2])                        # 연말정산처럼 1~2월에 몰림
        self.assertNotIn(3, low)
        self.assertIn(7, low)
        self.assertEqual(h.season({"2026-01": 50.0}), ([], []))  # 1년치 안 되면 계절 판단 안 함
        g = h.gold_score
        self.assertAlmostEqual(g({"docs": 1000}, 5000), 3.699, places=2)               # 황금지수 5 (≥0.3) → 만점
        self.assertAlmostEqual(g({"docs": 50000}, 5000), 3.699 * (0.1 / 0.3), places=2)  # 지수 0.1
        self.assertAlmostEqual(g({"docs": 500000}, 20000), 4.301 * (0.04 / 0.3), places=2)
        self.assertGreater(g({"docs": 1000}, 5000), g({"docs": 10}, 50))               # 같은 지수면 검색량 큰 쪽 (검색 50·글 10 이 1등 아님)
        self.assertEqual(g({"docs": 0, "hits": 0}, 9000), 0.0)                        # 글 0개(성인어 등)는 가짜 황금
        self.assertAlmostEqual(g({"docs": 1000, "hits": 30, "old": 30}, 5000), g({"docs": 1000}, 5000))  # 제목 일치는 참고만 (점수 무관)
        self.assertEqual(h.gold_label({"docs": 3000}, 1000), "황금")     # 지수 0.33
        self.assertEqual(h.gold_label({"docs": 3334}, 1000), "좋음")     # 지수 0.2999 (0.3 경계 바로 아래)
        self.assertEqual(h.gold_label({"docs": 10000}, 1000), "좋음")    # 지수 0.1 경계
        self.assertEqual(h.gold_label({"docs": 10001}, 1000), "보통")
        self.assertEqual(h.gold_label({"docs": 100001}, 1000), "포화")   # 지수 0.01 미만
        self.assertEqual(h.gold_label({"docs": 5}, 1000), "확인 필요(글 거의 없음)")
        for product in ("스케쳐스윈터부츠", "아크테릭스세륨후디", "카시오F91W", "캐나다구스롯지"):  # 11/5 첫 조회에서 황금으로 올라온 상품명
            self.assertFalse(h.is_info(product), product)
        for info in ("연차개수계산기", "육아휴직급여신청", "실업급여조건", "근로장려금"):
            self.assertTrue(h.is_info(info), info)
        full = h.signal_score({"docs": 100}, 9000, 7)
        self.assertAlmostEqual(h.signal_score({"docs": 100}, 9000, 7, "카시오F91W"), full * 0.5)   # 애매(상품명일 수도) → 버리지 않고 절반
        self.assertEqual(h.signal_score({"docs": 100}, 9000, 7, "홈택스로그인"), 0.0)             # 사이트 찾아가기 → 없음
        self.assertEqual(h.intent("카시오F91W"), "unknown")
        for domain in ("청약통장", "육아휴직", "자동차세", "국가장학금", "전입신고"):  # 수식어 없어도 제도 이름이면 정보 검색
            self.assertTrue(h.is_info(domain), domain)
        for shop in ("아로나민골드가격", "비맥스제트가격", "나이키v2k런", "치폴레뜻", "제네시스견적내기"):  # 11/5 두 번째 집계 상위 — 쇼핑·사전
            self.assertFalse(h.is_info(shop), shop)
        for widget in ("환율계산기", "택배조회", "금1돈시세", "연금복권", "날짜계산기"):  # 11/5 첫 집계 상위 — 네이버가 바로 답함
            self.assertFalse(h.is_info(widget), widget)
        self.assertTrue(h.is_navigational("홈택스 로그인"))
        self.assertTrue(h.is_navigational("국민연금공단고객센터전화번호"))
        self.assertFalse(h.is_navigational("연말정산 계산기"))
        sig = {"docs": 5000, "hits": 6, "old": 3, "peak": "1,2", "low": "6,7,8"}
        gs = 3 * (0.2 / 0.3)                                                   # 검색 1,000 ÷ 글 5,000 = 지수 0.2
        self.assertAlmostEqual(h.signal_score(sig, 1000, 12), 2 * gs + 1.0)    # 황금 점수×2 + 다음 달 성수기
        self.assertAlmostEqual(h.signal_score(sig, 1000, 12, "홈택스 로그인"), 1.0)   # 사이트 찾아가는 검색은 황금 가점 없음
        self.assertAlmostEqual(h.signal_score({"docs": 50000, "low": "7"}, 1000, 7), 2 * 3 * (0.02 / 0.3) - 0.5)
        self.assertEqual(h.signal_score(None, 1000, 1), 0.0)
        self.assertEqual(h.describe(sig, 1000), f"좋음 {gs:.1f} / 성수기 1·2월 / 황금지수 0.20(검색 1,000/글 5,000) / 참고: 상위 30개 중 제목 일치 6개(1년 넘은 글 3개)")

    def test_gov_vocab_catches_new_program_names(self):
        """정부 혜택 이름에서 뽑은 제도 단어로 수식어 없는 새 제도 이름도 정보 검색으로 (고정 목록이 놓치는 것)."""
        import datetime as dt
        import tempfile
        from bot import naver_hub as h
        pages = {1: {"data": [{"서비스명": "청년도약계좌 지원"}, {"서비스명": "첫만남이용권(출생 축하)"}, {"서비스명": "농업인 서비스"}]}}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vocab.json"
            msg = h.refresh_vocab(dt.date(2026, 11, 5), fetch=lambda kind, page, perPage: pages.get(page), path=path)
            self.assertIn("3개 이름에서", msg)
            with mock.patch.object(h, "GOV_VOCAB", path):
                h._gov_words.cache_clear()
                self.assertEqual(h.intent("청년도약계좌"), "info")       # 고정 단어 목록에 없는 새 제도
                self.assertEqual(h.intent("첫만남이용권"), "info")
                self.assertEqual(h.intent("서비스맛집"), "unknown")  # '서비스' 같은 흔한 말은 사전에서 뺌
                self.assertIn("최근 받음", h.refresh_vocab(dt.date(2026, 11, 7), fetch=None, path=path))  # 일주일 안엔 다시 안 받음
            h._gov_words.cache_clear()

    def test_daily_limit_per_kind(self):
        import datetime as dt
        import tempfile
        from bot import naver_hub as h
        with tempfile.TemporaryDirectory() as d:
            today = dt.date(2026, 11, 5)
            h._spend(today, h.LIMITS["blog"], "blog", Path(d))
            with self.assertRaises(h.LimitReached):
                h._spend(today, 1, "blog", Path(d))
            h._spend(today, 1, "trend", Path(d))              # 종류가 다르면 따로 셈
            h._spend(dt.date(2026, 11, 6), 1, "blog", Path(d))  # 다음 날은 다시 0부터

    def _db(self):
        import sqlite3
        from bot import keyword_db
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(keyword_db.SCHEMA)
        keyword_db._migrate(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS gsc_signals (norm TEXT PRIMARY KEY, kind TEXT, note TEXT)")
        rows = [("연말정산 계산기", 5000, "2026-11-01"), ("캠핑 준비물", 9000, "2026-11-01"), ("근로장려금 신청", 3000, "2026-11-05")]
        for kw, total, day in rows:
            conn.execute("INSERT INTO keywords(keyword, norm, total, first_seen, last_checked) VALUES (?,?,?,?,?)",
                         (kw, keyword_db.norm(kw), total, day, day))
        return conn

    def test_scan_golden_money_first_and_candidate_pool(self):
        import datetime as dt
        from bot import naver_hub as h, select_keyword
        from bot.settings import BotSettings
        conn = self._db()
        settings = BotSettings(min_volume=500, max_volume=30000)
        conn.execute("INSERT INTO keywords(keyword, norm, total, first_seen, last_checked) VALUES ('홈택스 로그인','홈택스로그인',20000,'2026-11-05','2026-11-05')")
        docs = {"연말정산 계산기": {"docs": 1000, "hits": 1, "old": 0}, "캠핑 준비물": {"docs": 900000, "hits": 28, "old": 0},
                "근로장려금 신청": {"docs": 50000, "hits": 20, "old": 0}}
        saved_batches = []

        def fake_totals(words, k, t, save=None):
            got = {w: docs[w] for w in words}
            saved_batches.append(list(words))
            if save:
                save(got)
            return got
        with mock.patch.object(h, "GOLD_SCAN", 2), mock.patch.object(h, "blog_totals", side_effect=fake_totals):
            msg = h.scan_golden(conn, settings, dt.date(2026, 11, 5), keys=("i", "k"))
        self.assertEqual(sorted(saved_batches[0]), ["근로장려금 신청", "연말정산 계산기"])  # 돈·계산기 먼저, 캠핑은 다음 날, 로그인은 안 봄
        self.assertIn("황금 1개", msg)                                       # 연말정산 계산기: 황금지수 5
        self.assertIn("연말정산계산기 3.7(검색 5,000/글 1,000)", msg)   # 황금지수 5 → 만점
        got = {c["keyword"]: c for c in select_keyword.ranked_candidates(conn, settings, limit=10)}
        self.assertIn("연말정산 계산기", got)                                # 오늘 수집분이 아니어도 황금이면 후보
        self.assertTrue(got["연말정산 계산기"]["naver"].startswith("황금 3.7"))
        self.assertNotIn("캠핑 준비물", got)                                 # 오늘 수집분도 황금도 아님
        self.assertIn("키 없음", h.scan_golden(conn, settings, dt.date(2026, 11, 5), keys=()))

    def test_longtail_heads_golden_first_and_prune_keeps_golden(self):
        from bot import discover, naver_hub as h
        conn = self._db()
        h.load(conn)
        conn.execute("INSERT INTO keywords(keyword, norm, total, first_seen, last_checked) VALUES ('육아휴직급여','육아휴직급여',1500,'2026-10-01','2026-10-01')")
        conn.execute("INSERT INTO naver_signals(norm, docs, hits, docs_checked) VALUES ('육아휴직급여', 3000, 4, '2026-11-05')")  # 지수 0.5 황금, 검색 1,500
        heads = discover._modifier_heads(conn, "2026-11-05", 10)
        self.assertEqual(heads[0], "육아휴직급여")      # 오늘 수집분·3,000 미만이어도 황금·좋음이면 롱테일 조합 먼저
        conn.execute("DELETE FROM keywords WHERE status IN ('new','ok') AND last_checked < ? AND norm NOT IN ("
                     " SELECT n.norm FROM naver_signals n JOIN keywords k ON k.norm = n.norm WHERE n.docs IS NOT NULL AND k.total >= n.docs * ?)",
                     ("2026-11-03", h.GOOD_INDEX))
        left = {r[0] for r in conn.execute("SELECT norm FROM keywords")}
        self.assertIn("육아휴직급여", left)               # 오래됐어도 황금·좋음은 남김
        self.assertNotIn("캠핑준비물", left)
        self.assertIn("k.total >= n.docs * ?", (ROOT / "bot" / "discover.py").read_text(encoding="utf-8"))

    def test_blog_totals_saves_chunks_and_stops_on_429(self):
        import datetime as dt
        import tempfile
        import urllib.error
        from bot import naver_hub as h
        calls = []

        def fake_call(path, keys, params=None, body=None):
            calls.append(params["query"])
            if params["query"] == "c":
                raise urllib.error.HTTPError("u", 429, "too many", {}, None)
            return {"total": 7, "items": []}
        saved = []
        with tempfile.TemporaryDirectory() as d, mock.patch.object(h, "COUNTER_DIR", Path(d)), \
                mock.patch.object(h, "CHUNK", 2), mock.patch.object(h, "WORKERS", 1), mock.patch.object(h, "_call", fake_call):
            got = h.blog_totals(["a", "b", "c", "d", "e"], ("i", "k"), dt.date(2026, 11, 5), save=saved.append)
        seven = {"docs": 7, "hits": 0, "old": 0}
        self.assertEqual(got, {"a": seven, "b": seven})  # 429 가 난 묶음부터 멈춤, 앞 묶음은 저장됨
        self.assertEqual(saved, [{"a": seven, "b": seven}])
        self.assertNotIn("e", calls)

    def test_head_on_competition_from_titles(self):
        """정면 경쟁 = 상위 30개 중 제목에 키워드가 그대로 든 글 수 (전체 문서 수는 붙여 쓴 말을 부풀려 안 씀, 11/5)."""
        import datetime as dt
        from bot import naver_hub as h
        items = [{"title": "<b>주휴수당</b> <b>계산</b> 방법", "postdate": "20240101"}, {"title": "주휴 수당계산기 총정리", "postdate": "20261001"},
                 {"title": "주휴수당 받는 조건", "postdate": "20261001"}, {"title": "알바 계산 실수", "postdate": "20200101"}]
        with mock.patch.object(h, "_call", return_value={"total": 400000, "items": items}):
            got = h._one_total("주휴수당계산", ("i", "k"), dt.date(2026, 11, 5))
        self.assertEqual(got, {"docs": 400000, "hits": 2, "old": 1})   # 제목 일치 2개, 그중 1년 넘은 글 1개

    def test_refresh_signals_trend_and_skip_recent(self):
        import datetime as dt
        from bot import naver_hub as h
        conn = self._db()
        today = dt.date(2026, 11, 5)
        series = {f"{y}-{m:02d}": (90.0 if m == 1 else 10.0) for y in (2025, 2026) for m in range(1, 13)}
        with mock.patch.object(h, "monthly_trend", return_value={"연말정산 계산기": series}) as trend, \
                mock.patch.object(h, "blog_totals", side_effect=lambda words, k, t, save=None: {w: 10 for w in words}):
            h.refresh_signals(conn, [{"keyword": "연말정산 계산기"}], today, keys=("i", "k"))
            self.assertEqual(h.load(conn)["연말정산계산기"]["peak"], "1")
            h.refresh_signals(conn, [{"keyword": "연말정산 계산기"}], today, keys=("i", "k"))
            self.assertEqual(trend.call_count, 1)               # 30일 안에 다시 조회 안 함
            h.refresh_signals(conn, [{"keyword": "연말정산 환급", "head": "연말정산 계산기"}], today, keys=("i", "k"))
            self.assertEqual(trend.call_args.args[0], ["연말정산 환급"])  # 대표 주제는 이미 받아 둔 것이라 키워드만


class CalcYearlyTest(unittest.TestCase):
    """계산기 연초 값 감시 (11/5): 11/15 부터 다음 해 값 확인이 안 된 계산기를 글쓰기 세션에 넘김 (bot/calc_watch.py)."""

    def _dir(self, files):
        import tempfile
        d = Path(tempfile.mkdtemp())
        for name, data in files.items():
            (d / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
        return d

    def test_due_switches_to_next_year_from_nov15(self):
        import datetime as dt
        from bot import calc_watch
        d = self._dir({"a": {"yearly_checked": 2026}, "b": {"yearly": False}, "c": {"yearly_checked": 2027}})
        self.assertEqual([i["id"] for i in calc_watch.due(dt.date(2026, 11, 14), d)], [])
        self.assertEqual([i["id"] for i in calc_watch.due(dt.date(2026, 11, 15), d)], ["a"])
        self.assertEqual([i["id"] for i in calc_watch.due(dt.date(2027, 1, 10), d)], ["a"])  # 해가 바뀌어도 남으면 계속
        (d / "a.json").write_text(json.dumps({"yearly_checked": 2026, "yearly_recheck": "2026-12-15"}), encoding="utf-8")
        self.assertEqual([i["id"] for i in calc_watch.due(dt.date(2026, 12, 14), d)], [])   # 확정 기다리는 중
        self.assertEqual([i["id"] for i in calc_watch.due(dt.date(2026, 12, 15), d)], ["a"])

    def test_law_change_flags_calculator_until_handled(self):
        import datetime as dt
        import tempfile
        from bot import calc_watch
        d = self._dir({"a": {"yearly": False, "sources": [{"name": "근로자퇴직급여 보장법 제8조", "url": "https://x"}]},
                       "b": {"yearly": False, "sources": [{"name": "국세청 안내", "url": "https://x"}], "laws": ["소득세법"]}})
        up = [{"law": "근로자퇴직급여 보장법", "date": "2027-01-01", "key": "근로자퇴직급여 보장법|2027-01-01", "articles": ["제8조"]},
              {"law": "소득세법", "date": "2026-11-01", "key": "소득세법|2026-11-01", "articles": []}]   # 이미 시행된 것은 빼고
        got = calc_watch.law_due(dt.date(2026, 11, 5), up, d)
        # 날짜로 거르지 않음 — 시행일이 지나도 처리될 때까지 남김 (run 이 calc_law_pending 으로 이어 줌, 11/5 검토)
        self.assertEqual([(i["id"], i["law_change"]) for i in got], [("a", "근로자퇴직급여 보장법|2027-01-01"), ("b", "소득세법|2026-11-01")])
        (d / "a.json").write_text(json.dumps({"yearly": False, "sources": [{"name": "근로자퇴직급여 보장법", "url": "https://x"}],
                                              "law_checked": ["근로자퇴직급여 보장법|2027-01-01"]}), encoding="utf-8")
        self.assertEqual([i["id"] for i in calc_watch.law_due(dt.date(2026, 11, 5), up, d)], ["b"])   # a 는 상관없다고 확인 → 다시 안 나옴
        self.assertIn("국민건강보험법 시행령", calc_watch.laws_in("국가법령정보센터 국민건강보험법 시행령 (보험료율)"))
        self.assertNotIn("국가법령정보센터 국민건강보험법 시행령", calc_watch.laws_in("국가법령정보센터 국민건강보험법 시행령"))

    def test_real_calculators_declare_yearly(self):
        from bot import calc_watch
        for path in calc_watch.DATA_DIR.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data.get("yearly") is False or isinstance(data.get("yearly_checked"), int), path.name)


class ChangedMarkTest(unittest.TestCase):
    """근거가 바뀐 글 표시 (11/5 운영자 '모든 글 업데이트 안 하나'): 혜택 내용·계산기 값·금리·법령이 바뀌면 글에 표시 →
    공개 글은 주당 한도와 상관없이 재검증, 초안은 공개 직전 보류."""

    def _wp(self, store):
        def wp(*a):
            if a[2] == "get":
                return store.get(a[3], "")
            store[a[3]] = a[5]
            return ""
        return wp

    def test_flag_keeps_unhandled_reasons(self):
        from bot import changed
        store = {"1": "2026-11-01 근로기준법"}
        self.assertEqual(changed.flag(["1"], "2026-11-05 혜택 내용 변경: 구직급여", self._wp(store)), ["1"])
        self.assertEqual(store["1"], "2026-11-05 혜택 내용 변경: 구직급여 / 2026-11-01 근로기준법")
        self.assertEqual(changed.flag(["1"], "2026-11-05 혜택 내용 변경: 구직급여", self._wp(store)), [])  # 같은 표시 두 번 안 함
        self.assertTrue(changed.pending(store["1"], "2026-11-04"))
        self.assertFalse(changed.pending(store["1"], "2026-11-05"))  # 그날 이후 확인했으면 처리됨

    def test_benefit_content_change_marks_posts(self):
        import datetime as dt
        import tempfile
        from bot import benefit_watch
        row = {"서비스ID": "S1", "서비스명": "국민취업지원제도", "지원내용": "월 50만 원 6개월"}
        rows = lambda r: (lambda path, page, perPage, **kw: {"data": [r] if kw["cond[소관기관유형::EQ]"] == "중앙행정기관" else []})  # noqa: E731
        bulk = {"7": {"status": "publish", "title": "국민취업지원제도 신청", "content": "..."},
                "8": {"status": "draft", "title": "실업급여", "content": "국민취업지원제도와 차이"},
                "9": {"status": "publish", "title": "연말정산", "content": "공제"}}
        snap = Path(tempfile.mkdtemp()) / "snap.json"
        store = {}
        msg = benefit_watch.watch_changes(dt.date(2026, 11, 5), rows(row), bulk, self._wp(store), snap)
        self.assertEqual(store, {})                       # 처음 본 혜택은 저장만
        self.assertIn("처음 저장 1", msg)
        benefit_watch.watch_changes(dt.date(2026, 11, 6), rows(row), bulk, self._wp(store), snap)
        self.assertEqual(store, {})                       # 내용 그대로면 표시 없음
        changed_row = {**row, "지원내용": "월 60만 원 6개월"}
        msg = benefit_watch.watch_changes(dt.date(2026, 11, 7), rows(changed_row), bulk, self._wp(store), snap)
        self.assertEqual(sorted(store), ["7", "8"])       # 이름이 나오는 공개 글·초안만
        self.assertIn("2026-11-07 혜택 내용 변경(정부24): 국민취업지원제도", store["7"])
        self.assertIn("재확인 표시 2편", msg)
        self.assertIn("API 못 받음", benefit_watch.watch_changes(dt.date(2026, 11, 8), lambda *a, **k: None, bulk, self._wp(store), snap))

    def test_calc_value_switch_marks_posts_with_old_value(self):
        import datetime as dt
        import tempfile
        from bot import calc_watch
        d = Path(tempfile.mkdtemp())
        (d / "overtime-pay.json").write_text(json.dumps({"basis_date": "2026-11-05", "min_wage": 10320, "rate": 0.0475,
            "scheduled": [{"from": "2027-01-01", "values": {"min_wage": 10700, "rate": 0.05}}]}), encoding="utf-8")
        bulk = {"1": {"status": "publish", "title": "최저임금", "content": "시급 10,320원"},
                "2": {"status": "publish", "title": "대출", "content": "금리 4.75%"},          # 계산기 없는 글의 흔한 비율은 안 건드림
                "3": {"status": "draft", "title": "연장수당", "content": '4.75% [firstlab_calc id="overtime-pay"]'},
                "4": {"status": "trash", "title": "x", "content": "10,320원"}}
        state, store = {}, {}
        self.assertEqual(calc_watch.mark_switches(dt.date(2026, 12, 31), state, bulk, self._wp(store), d), [])  # 시행 전
        logs = calc_watch.mark_switches(dt.date(2027, 1, 1), state, bulk, self._wp(store), d)
        self.assertEqual(sorted(store), ["1", "3"])
        self.assertIn("min_wage 10320→10700", store["1"])
        self.assertIn("2편", logs[0])
        self.assertEqual(calc_watch.mark_switches(dt.date(2027, 1, 2), state, bulk, self._wp(store), d), [])  # 한 번만

    def test_draft_with_new_mark_is_held_before_publish(self):
        import datetime as dt
        from bot import changed, freshness, source_check

        def wp(*a):
            if a[:3] == ("post", "meta", "get"):
                return {freshness.VERIFIED_META: "2026-11-01", changed.META: "2026-11-05 혜택 내용 변경(정부24): 구직급여",
                        source_check.META: json.dumps({"found": [{"claim": "c", "numbers": ["1"], "urls": ["u"]}]})}.get(a[4], "")
            return "x"
        with mock.patch.object(freshness, "wp_cli", side_effect=wp), mock.patch.object(freshness, "problems", side_effect=lambda *a: []):
            self.assertEqual(freshness.check_post(1, dt.date(2026, 11, 6)), ["쓴 뒤 근거 변경: 2026-11-05 혜택 내용 변경(정부24): 구직급여"])
            self.assertEqual(freshness.check_post(1, dt.date(2026, 11, 4)), [])   # 아직 안 온 날짜 표시는 무시

    def test_post_without_evidence_is_held(self):
        """근거 기록(근거 대조·검수 기록)이 하나도 없는 글은 공개 전에 보류 (11/5 운영자 '예전 봇 글도 근거 기록')."""
        import datetime as dt
        from bot import freshness
        with mock.patch.object(freshness, "wp_cli", side_effect=lambda *a: "2026-11-01" if "--field=post_date" in a else ""), \
                mock.patch.object(freshness, "_review_from_queue", return_value=[]):
            self.assertIn("근거 기록 없음 (어느 공식 출처로 확인했는지 남기기)", freshness.check_post(1, dt.date(2026, 11, 6)))

    def test_recheck_without_changes_records_date(self):
        from bot import refresh
        calls = []

        def wp(*a):
            calls.append(a)
            return "2026-11-05 10:00:00" if "--field=post_modified" in a else ("<p>본문</p>" if "--field=post_content" in a else "제목")
        with mock.patch.object(refresh, "wp_cli", side_effect=wp):
            with self.assertRaises(RuntimeError):
                refresh.apply_direct({"post_id": 3}, {})
            refresh.apply_direct({"post_id": 3, "checked": True, "changes": "확인 결과 그대로"}, {})
        self.assertTrue(any(a[:5] == ("post", "meta", "update", "3", refresh.META_KEY) for a in calls))
        self.assertFalse(any(a[:2] == ("post", "update") for a in calls))   # 본문·제목은 그대로

    def test_refresh_fixes_beyond_weekly_cap(self):
        from bot import refresh
        posts = [{"id": 1, "title": "a", "url": "u1", "date": "2026-10-01", "refreshed": "2026-11-01", "verified": "",
                  "law_changed": "2026-11-05 혜택 내용 변경: x", "has_number": True},
                 {"id": 2, "title": "b", "url": "u2", "date": "2026-01-01", "refreshed": "", "verified": "2026-11-06",
                  "law_changed": "2026-11-05 근로기준법", "has_number": True}]   # 표시 뒤에 검수 통과 → 처리됨
        conn = sqlite3.connect(":memory:")
        with mock.patch.object(refresh, "wp_cli", return_value=json.dumps(posts)):
            picks = refresh.pick_targets(conn, 5, fixes_only=True)
        self.assertEqual([(p["id"], mode) for p, mode, _ in picks], [(1, "recheck")])


class GoldenNoteTest(unittest.TestCase):
    """새 글 기록에 황금 키워드 정보 (11/5 운영자 '김해공항 주차장 황금 키워드인가?')."""

    def test_note(self):
        from bot import naver_hub, publish_queue
        conn = sqlite3.connect(":memory:")
        self.assertEqual(publish_queue.golden_note(conn, "없는키워드", 500), "네이버 신호 없음 (검색량 500)")
        conn.execute("INSERT INTO naver_signals (norm, docs, docs_checked) VALUES ('국민연금수령나이', 3000, '2026-11-05')")
        self.assertIn("황금지수 1.33(검색 4,000/글 3,000)", publish_queue.golden_note(conn, "국민연금 수령나이", 4000))


class LearnedBrandTest(unittest.TestCase):
    """글쓰기 세션이 브랜드·회사 상품이라 건너뛴 브랜드를 배워 같은 브랜드 후보를 막음 (11/5)."""

    def test_learn_and_block(self):
        import tempfile
        from bot import keyword_filter as kf
        path = Path(tempfile.mkdtemp()) / "brands.json"
        self.assertEqual(kf.learn_brands(["SBI저축은행", "폴로", "SBI 저축은행", "삼성화재"], path), ["sbi저축은행"])  # 짧은 말·이미 있는 브랜드 제외
        self.assertEqual(kf.learned_brands(path), ["sbi저축은행"])
        with mock.patch.object(kf, "LEARNED_BRANDS", path):
            kf._learned_cache.clear()
            self.assertTrue(kf.is_brand("SBI저축은행대환대출"))
            self.assertFalse(kf.is_brand("저축은행 대환대출 조건"))
        kf._learned_cache.clear()

    def test_skip_rejects_same_brand_candidates(self):
        import tempfile
        from bot import keyword_filter as kf, publish_queue
        path = Path(tempfile.mkdtemp()) / "brands.json"
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE keywords (keyword TEXT, norm TEXT, status TEXT, reason TEXT)")
        for k in ("SBI저축은행신용대출", "SBI저축은행대환대출", "저축은행대환대출"):
            conn.execute("INSERT INTO keywords VALUES (?, ?, 'new', '')", (k, k.lower()))
        ctx = mock.MagicMock()
        ctx.__enter__.return_value = conn
        with mock.patch.object(kf, "LEARNED_BRANDS", path), mock.patch.object(publish_queue, "connect", return_value=ctx):
            msg = publish_queue.skip_keywords({"keywords": [{"keyword": "SBI저축은행신용대출", "why": "특정 회사 상품", "brand": "SBI저축은행"}]})
        status = {r["keyword"]: r["status"] for r in conn.execute("SELECT keyword, status FROM keywords")}
        self.assertEqual(status, {"SBI저축은행신용대출": "rejected", "SBI저축은행대환대출": "rejected", "저축은행대환대출": "new"})
        self.assertIn("같은 브랜드 후보 1개 제외", msg)
        kf._learned_cache.clear()


class SourceRecheckTest(unittest.TestCase):
    """공개 뒤에도 근거를 다시 봄 (11/5 운영자 '어느 법령·규정으로 만들어졌고 바뀌면 고치는 게 시스템화돼 있나')."""

    def test_calc_source_page_number_disappears(self):
        import tempfile
        from bot import calc_watch
        d = Path(tempfile.mkdtemp())
        anchors = d / "anchors.json"
        data = {"basis_date": "2026-11-05", "fee": 300, "max_day": 10000, "rate": 0.5,
                "sources": [{"name": "공항 요금표", "url": "https://airport.example/fee"}, {"name": "법", "url": "https://www.law.go.kr/x"}]}
        (d / "parking.json").write_text(json.dumps(data), encoding="utf-8")
        page = {"text": "기본 30분 900원 매 10분 300원 24시간 10000원"}
        fetch = lambda url: page["text"] if "airport" in url else None  # noqa: E731
        self.assertEqual(calc_watch.source_changes(d, fetch, anchors), [])          # 처음: 기준만 저장 (300·10000)
        self.assertEqual(json.loads(anchors.read_text())["parking"]["pages"]["https://airport.example/fee"], ["10000", "300"])
        self.assertEqual(calc_watch.source_changes(d, fetch, anchors), [])          # 그대로면 없음
        page["text"] = "기본 30분 1000원 매 10분 400원 24시간 12000원"
        got = calc_watch.source_changes(d, fetch, anchors)
        self.assertEqual([(g["id"], sorted(g["missing"])) for g in got], [("parking", ["10000", "300"])])
        self.assertEqual(calc_watch.source_changes(d, lambda url: None, anchors), [])  # 못 열면 판단 안 함
        data.update(basis_date="2026-11-20", fee=400, max_day=12000)                 # 글쓰기 세션이 고침 → 기준 새로
        (d / "parking.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(calc_watch.source_changes(d, fetch, anchors), [])

    def test_published_posts_rechecked_in_turn(self):
        import datetime as dt
        from bot import changed, freshness, recheck, source_check
        self.assertEqual(recheck.batch(["5", "1", "9", "3"], "3", 2), ["5", "9"])
        self.assertEqual(recheck.batch(["5", "1", "9", "3"], "9", 2), ["1", "3"])   # 끝까지 가면 처음부터
        record = {"found": [{"claim": "주차 10분 300원", "numbers": ["300"], "urls": ["https://airport.example/fee"]}]}
        metas = {("7", source_check.META): json.dumps(record), ("7", freshness.VERIFIED_META): "2026-11-01"}
        store = {}

        def wp(*a):
            if a[2] == "get":
                return store.get(a[3]) if a[4] == changed.META and a[3] in store else metas.get((a[3], a[4]), "")
            store[a[3]] = a[5]
            return ""
        bulk = {"7": {"status": "publish", "name": "x", "title": "", "content": ""}, "8": {"status": "draft", "name": "y"}}
        state = {"log": []}
        with mock.patch("bot.state.load_state", return_value=state), mock.patch("bot.state.save_state"):
            msg = recheck.run(dt.date(2026, 11, 20), bulk, wp, lambda url: "매 10분 400원")
        self.assertIn("2026-11-20 근거 다시 보기", store["7"])
        self.assertNotIn("8", store)                         # 초안은 공개 직전 확인이 따로 봄
        self.assertIn("표시 1편", msg)
        self.assertEqual(state[recheck.CURSOR], "7")


class ListedCasesTest(unittest.TestCase):
    """조문이 나열한 경우(각 호)를 글쓰기 세션에 보여 줌 — 일부만 쓰는 조건 누락 (11/5 #651 유산·사산휴가, #687 대체공휴일 3호)."""

    def test_cases_from_article(self):
        from bot import source_check
        body = ("제3조(대체공휴일) ① 공휴일이 다음 각 호의 어느 하나에 해당하는 경우에는 다음의 첫 번째 비공휴일을 대체공휴일로 한다. "
                "1. 토요일이나 일요일과 겹치는 경우 2. 일요일과 겹치는 경우 3. 토요일ㆍ일요일이 아닌 날에 다른 공휴일과 겹치는 경우 ② 그 밖의 사항")
        cases = source_check.listed_cases(body)
        self.assertEqual(len(cases), 3)
        self.assertIn("다른 공휴일과 겹치는 경우", cases[2])
        self.assertEqual(source_check.listed_cases("제1조(목적) 이 법은 목적이 있다."), [])
        got = source_check.law_exceptions([{"claim": "대체공휴일 (제3조)", "urls": ["https://www.law.go.kr/법령/관공서의공휴일에관한규정/제3조"],
                                            "verdict": "맞음", "action": "유지"}], fetcher=lambda url: body)
        self.assertEqual(len(got[0]["cases"]), 3)


class EvidenceFileTest(unittest.TestCase):
    """예전 봇 글 근거 기록 파일 (11/5 운영자): 근거 대조를 저장하고 확인한 날을 남김."""

    def test_save_evidence(self):
        from bot import publish_queue
        calls = []
        with mock.patch.object(publish_queue, "wp_cli", side_effect=lambda *a: calls.append(a) or ""), \
                mock.patch.object(publish_queue.source_check, "check", return_value={"counts": {}, "claims": [], "found": [], "laws": []}), \
                mock.patch.object(publish_queue.freshness, "mark_verified") as verified:
            msg = publish_queue.save_evidence({"type": "evidence", "post_id": 85, "keyword": "하이패스카드신청",
                                               "review": [{"claim": "c", "urls": ["https://www.law.go.kr/법령/유료도로법시행령/제8조"], "verdict": "맞음", "action": "유지"}]})
        self.assertTrue(any(a[:4] == ("post", "meta", "update", "85") and a[4] == publish_queue.source_check.META for a in calls))
        verified.assert_called_once_with("85")
        self.assertIn("글 85", msg)
        with self.assertRaises(ValueError):
            publish_queue.save_evidence({"post_id": 1, "review": []})

    def test_real_evidence_files_point_to_posts(self):
        for path in sorted((ROOT / "wordpress" / "queue").glob("evidence-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["type"], "evidence")
            self.assertTrue(data["keyword"] and data["review"], path.name)


class ConditionGateTest(unittest.TestCase):
    """조건 누락을 관문이 막음 (11/5 운영자 '막을 대책 세워서 어떻게든 해야지, 평생 니가 고칠 거가')."""

    def test_headline_number_only_true_with_scope(self):
        from bot import condition_gate as g
        body = ("<p>P1·P2 여객주차장은 월~목 10,000원, P3는 7,000원이에요.</p><p>P1·P2 3박 4일은 목요일 10,000원에 금요일 15,000원을 더해요.</p>"
                "<p>P3에 세우면 7,000원 + 10,000원이에요.</p>")
        self.assertTrue(g.headline_scope("김해공항 주차장 요금: 월~목 하루 최대 10,000원", "", body))      # #655
        self.assertEqual(g.headline_scope("김해공항 주차장 요금: P1·P2 월~목 하루 최대 10,000원", "", body), [])
        body2 = "<p>7월 1일 입사면 다음 해 비례 연차는 약 7.6일이에요.</p><p>7월 1일부터 184일이라 약 7.6일이에요.</p>"
        self.assertTrue(g.headline_scope("회계연도 연차: 7월 입사면 비례 연차 7.6일", "", body2))           # #651
        self.assertEqual(g.headline_scope("회계연도 연차: 7월 1일 입사면 비례 연차 7.6일", "", body2), [])
        self.assertEqual(g.headline_scope("설날 2월 17일", "", "<p>2026년 설날은 2월 17일, 음력 1월 1일이에요.</p>"), [])  # 날짜 자체는 비교 안 함
        self.assertEqual(g.headline_scope("연차 15일", "", "<p>1년 80% 출근하면 15일이에요.</p>"), [])     # 조건 없는 값

    def test_exception_in_other_article(self):
        from bot import condition_gate as g
        law = {"제53조": "제53조(증여재산 공제) 거주자가 증여를 받은 경우에는 다음 각 호의 금액을 공제한다.",
               "제53조의2": "제53조의2(혼인ㆍ출산 증여재산 공제) ① 혼인일 전후 2년 이내에 증여를 받는 경우에는 제2항 및 제53조제2호에 따른 공제와 별개로 1억원을 공제한다.",
               "제54조": "제54조(준용규정) 제53조를 준용한다."}
        self.assertEqual(g.related_exceptions(law, "제53조"), [("제53조의2", "혼인ㆍ출산 증여재산 공제")])
        review = [{"claim": "부모·자녀 5,000만 원 (상속세및증여세법 제53조)", "urls": ["https://www.law.go.kr/법령/상속세및증여세법"],
                   "verdict": "맞음", "action": "유지"}]
        got = g.exception_articles(review, lambda url: law)                                          # #640
        self.assertEqual(len(got), 1)
        self.assertIn("제53조의2(혼인ㆍ출산 증여재산 공제)", got[0])
        checked = review + [{"claim": "제53조의2 혼인·출산 공제 해당 없음 — 당첨금 증여는 혼인과 무관한 경우가 대부분이라 안내만",
                             "urls": ["https://www.law.go.kr/법령/상속세및증여세법/제53조의2"], "verdict": "맞음", "action": "유지"}]
        self.assertEqual(g.exception_articles(checked, lambda url: law), [])
        self.assertEqual(g.exception_articles(review, lambda url: {}), [])                           # 못 열면 막지 않음


class HoldRetryTest(unittest.TestCase):
    """보류 글이 평생 보류로 남거나 다른 보류 글의 차례를 막지 않게 (11/5 운영자 '공개 막으면 평생 보류로 있나?')."""

    def _wp(self, meta):
        def wp(*a):
            if a[:2] == ("post", "get"):
                return {"--field=post_status": "draft", "--field=post_modified": "m",
                        "--field=post_content": "<p>" + "가" * 100 + "</p>", "--field=post_title": "제목", "--field=post_excerpt": "요약"}.get(a[3], "")
            if a[:3] == ("post", "meta", "get"):
                return meta.get(a[4], "")
            if a[:3] == ("post", "meta", "update"):
                meta[a[4]] = a[5]
            return ""
        return wp

    def test_failed_fixes_counted_and_give_up(self):
        from bot import hold
        meta = {}
        with mock.patch.object(hold, "wp_cli", side_effect=self._wp(meta)), \
                mock.patch.object(hold.review_gate, "problems", return_value=["검수 기록 없음"]):
            for i in range(1, 4):
                msg = hold.apply_fix({"post_id": 5, "base_modified": "m", "content_html": "<p>" + "가" * 100 + "</p>", "review": []})
                self.assertTrue(meta[hold.TRIES_META].startswith(f"{i}|"))
            self.assertIn("30일 뒤 다시", msg)                                   # 3번째에 쉬게 됨
        meta2 = {}
        with mock.patch.object(hold, "wp_cli", side_effect=self._wp(meta2)):
            msg = hold.apply_fix({"post_id": 6, "give_up": True, "why": "공식 근거 없음"})
        self.assertTrue(meta2[hold.TRIES_META].startswith(f"{hold.MAX_TRIES}|"))
        self.assertIn("포기", meta2[hold.HOLD_META if hasattr(hold, "HOLD_META") else hold.review_gate.HOLD_META])
        self.assertIn("30일 뒤 다시", msg)

    def test_php_skips_resting_posts(self):
        php = (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8")
        self.assertIn("firstlab_api_hold_resting( $post->ID )", php)
        self.assertIn("'_firstlab_hold_tries'", php)


class ReviewQueueTest(unittest.TestCase):
    """대기 글이 많으면 새 글 대신 외부 검수 안 된 대기 글을 검수 (11/5 운영자 '니가 없어도 돌아가게')."""

    def test_pick(self):
        import tempfile
        from bot import review_queue as rq
        path = Path(tempfile.mkdtemp()) / "reviewed.txt"
        path.write_text("10 첫 글\n11 둘째 (재검수)\ncalc:cbm\n", encoding="utf-8")
        self.assertEqual(rq.reviewed_ids(path), {10, 11})
        ready = [{"post_id": i, "title": f"t{i}"} for i in range(10, 10 + rq.REVIEW_WHEN_READY)]
        got = rq.pick(ready, rq.reviewed_ids(path))
        self.assertEqual((got["post_id"], got["unreviewed"]), (12, rq.REVIEW_WHEN_READY - 2))
        self.assertIsNone(rq.pick(ready[:-1], set()))                       # 대기 글이 적으면 새 글 쓰기
        self.assertIsNone(rq.pick(ready, {r["post_id"] for r in ready}))     # 다 검수했으면 새 글 쓰기

    def test_monitor_reviews_until_dec5(self):
        """11/5 운영자: 12/5 까지 작성자는 새 글만, 대기 글 검수는 감시 세션 — 감시가 멈춰 쌓이면 작성자가 다시, 12/5 뒤엔 원래대로."""
        from bot import review_queue as rq
        ready = [{"post_id": i, "title": f"t{i}"} for i in range(100, 140)]
        few = set(range(100, 135))                                         # 검수 안 된 것 5편
        self.assertIsNone(rq.pick(ready, few, "2026-11-06"))
        self.assertIsNotNone(rq.pick(ready, set(range(100, 115)), "2026-11-06"))   # 25편 밀림 → 작성자가 맡음
        self.assertEqual(rq.pick(ready, few, "2026-12-05")["post_id"], 135)        # 12/5 부터 원래대로
        self.assertEqual(rq.pick(ready, few)["post_id"], 135)                      # 날짜 없이 부르면 예전 그대로

    def test_any_run_end_counts_as_done(self):
        """보류 수정·계산기만 한 실행도 끝남으로 (11/5 22:13 실행 74번이 보류 수정만 하고 47분 멈춤)."""
        import tempfile
        from bot import publish_queue, run
        state = {"log": [{"time": "2026-11-05 22:24", "ok": True, "msg": "글쓰기 실행 끝(직접): 보류 초안 #784 계산기 검수"}]}
        self.assertTrue(run.post_arrived_since(state, "2026-11-05 22:13", seen="2026-11-05 22:12 직접 작성 글: x"))
        self.assertIn('"type": "run_done"', (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))
        d = Path(tempfile.mkdtemp())
        f = d / "done-1.json"
        f.write_text(json.dumps({"type": "run_done", "summary": "보류 수정"}), encoding="utf-8")
        st = {"log": []}
        with mock.patch.object(publish_queue, "pending", return_value=[f]), mock.patch.object(publish_queue, "load_settings"), \
                mock.patch.object(publish_queue, "load_state", return_value=st), mock.patch.object(publish_queue, "save_state"), \
                mock.patch.object(publish_queue, "DONE_DIR", d / "done"), mock.patch.object(publish_queue.related, "allowed_urls", return_value=set()), \
                mock.patch.object(publish_queue, "_marker", side_effect=lambda p: d / (p.name + ".ok")), \
                mock.patch.object(publish_queue, "add_log", side_effect=lambda s, ok, m: s["log"].insert(0, {"time": "t", "ok": ok, "msg": m})):
            publish_queue.main()
        self.assertTrue(st["log"][0]["msg"].startswith("글쓰기 실행 끝(직접):"), st["log"])
        self.assertIn("last_done_at", st)

    def test_review_run_counts_as_done(self):
        from bot import run
        state = {"log": [{"time": "2026-11-05 19:00", "ok": True, "msg": "대기 글 검수(직접): 글 12 't' 고친 곳 3개"}]}
        self.assertTrue(run.post_arrived_since(state, "2026-11-05 18:00", seen=""))


class DeployWatchTest(unittest.TestCase):
    """배포 밀림 감지 (감시 세션 없이) — 서버 버전이 60분 넘게 안 바뀌고 main 이 앞서면 알림."""

    def test_alert_after_limit(self):
        from datetime import datetime, timedelta
        from bot import deploy_watch as dw
        state, t0 = {}, datetime(2026, 11, 5, 10, 0)
        self.assertIsNone(dw.check(state, t0, "aaa", "bbb"))
        self.assertIsNone(dw.check(state, t0 + timedelta(minutes=50), "aaa", "ccc"))      # main 이 움직여도 서버가 그대로면 시계 계속
        self.assertIn("61분째", dw.check(state, t0 + timedelta(minutes=61), "aaa", "ccc"))
        self.assertIn("deploy_alert", state)
        self.assertIsNone(dw.check(state, t0 + timedelta(minutes=70), "ccc", "ccc"))      # 올라가면 풀림
        self.assertNotIn("deploy_alert", state)
        self.assertIsNone(dw.check(state, t0 + timedelta(minutes=80), "ccc", "ddd"))
        self.assertIsNone(dw.check(state, t0 + timedelta(minutes=125), "ddd", "eee"))     # 배포가 진행 중이면 알림 없음

    def test_kst_aware_time_from_bot(self):
        """봇은 datetime.now(KST)(시간대 있음)를 넘김 — 11/5 검토에서 TypeError 로 봇이 멈출 뻔함."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        from bot import deploy_watch as dw
        kst, state = ZoneInfo("Asia/Seoul"), {}
        self.assertIsNone(dw.check(state, datetime(2026, 11, 5, 10, 0, tzinfo=kst), "aaa", "bbb"))
        self.assertIn("분째", dw.check(state, datetime(2026, 11, 5, 11, 5, tzinfo=kst), "aaa", "bbb"))


class StateThreeWayTest(unittest.TestCase):
    """11/5 검토: 몇 분 들고 있던 옛 상태를 저장하면 그사이 봇이 적은 오늘 공개·실행 기록이 지워짐 → 바꾼 키만 덮어씀."""

    def test_only_changed_keys_overwrite(self):
        from bot import state as st
        base = {"done": {}, "direct_fired": {}, "rates": {"base": 3.0}, "log": [], "used_keywords": []}
        mine = json.loads(json.dumps(base))
        mine["rates"] = {"base": 2.75}                      # 내가 바꾼 것
        stored = {"done": {"2026-11-05": ["09:00"]}, "direct_fired": {"2026-11-05": ["07:40"]}, "rates": {"base": 3.0},
                  "log": [{"time": "2026-11-05 09:00", "ok": True, "msg": "공개"}], "used_keywords": []}
        merged = st.changed_onto(mine, base, stored)
        self.assertEqual(merged["done"], {"2026-11-05": ["09:00"]})        # 다른 실행이 적은 공개 기록 유지
        self.assertEqual(merged["direct_fired"], {"2026-11-05": ["07:40"]})
        self.assertEqual(merged["rates"], {"base": 2.75})

    def test_read_failure_is_not_empty_state(self):
        import subprocess
        from bot import state as st
        err = subprocess.CalledProcessError(1, ["wp"], "", "Error: Database is locked")
        with mock.patch.object(st, "wp_cli", side_effect=err):
            with self.assertRaises(subprocess.CalledProcessError):
                st.load_state()
        missing = subprocess.CalledProcessError(1, ["wp"], "", "Error: Could not get 'firstlab_bot_state' option. Does it exist?")
        with mock.patch.object(st, "wp_cli", side_effect=missing):
            self.assertEqual(st.load_state()["log"], [])


class PipelineReviewFixesTest(unittest.TestCase):
    """11/5 전체 검토에서 찾은 공개 단계 결함 (한 번 고친 뒤 다시 안 생기게)."""

    def test_structure_numbers_ignored_in_title_check(self):
        from bot.writer import numbers_match
        self.assertTrue(numbers_match("캠핑 준비물 체크리스트 7가지", "<p>텐트와 침낭</p>"))
        self.assertFalse(numbers_match("과태료 30만 원", "<p>과태료가 있어요</p>"))

    def test_different_numbers_are_not_similar_topic(self):
        from bot import publisher
        posts = json.dumps([{"ID": 1, "post_name": "2026최저임금"}])
        with mock.patch.object(publisher, "wp_cli", return_value=posts):
            self.assertEqual(publisher.similar_post("2027최저임금"), ("", ""))
            self.assertEqual(publisher.similar_post("2026최저임금표")[0], "1")

    def test_slug_key_matches_wordpress_slug(self):
        from bot.freshness import _slug_key
        self.assertEqual(_slug_key("ITQ자격증조회"), _slug_key("itq자격증조회"))
        self.assertEqual(_slug_key("HS CODE 조회"), _slug_key("hs-code-조회"))

    def test_failing_queue_file_gives_up(self):
        import tempfile
        from bot import publish_queue as pq
        d = Path(tempfile.mkdtemp())
        f = d / "x.json"
        f.write_text("{}", encoding="utf-8")
        with mock.patch.object(pq, "DONE_DIR", d / "done"):
            self.assertEqual([pq._bump_file_fail(f) for _ in range(3)], [1, 2, 3])

    def test_changed_mark_latest_date(self):
        from bot import changed
        self.assertTrue(changed.pending("2026-10-25 고용보험법 / 2026-11-05 기준금리", "2026-10-30"))


class ReviewDoneConsistencyTest(unittest.TestCase):
    """대기 글 검수 기록 파일이 있으면 reviewed.txt 에도 그 글이 있어야 함 (11/5 검토: 빠지면 같은 글을 매번 다시 검수)."""

    def test_reviewdone_files_recorded(self):
        from bot.review_queue import reviewed_ids
        done = reviewed_ids()
        for path in (ROOT / "wordpress" / "queue").glob("reviewdone-*.json"):
            pid = int(json.loads(path.read_text(encoding="utf-8"))["post_id"])
            self.assertIn(pid, done, f"{path.name}: automation/reviewed.txt 에 {pid} 없음 — 커밋에서 빠짐")


class HealthAlertTest(unittest.TestCase):
    """감시 세션·Claude 없이도 멈춤을 알리는 통로 (/health + GitHub Actions 메일, 11/5 검토)."""

    def test_secrets_expiring(self):
        import datetime as dt
        import tempfile
        from bot import config, discover
        p = Path(tempfile.mkdtemp()) / "s.env"
        p.write_text("DATA_GO_KR_KEY=abc\nDATA_GO_KR_KEY_EXPIRES=2026-12-01\nCF_EXPIRES=2027-06-01\n", encoding="utf-8")
        with mock.patch.object(config, "SECRETS_FILE", p):
            self.assertEqual(discover.secrets_expiring(dt.date(2026, 11, 10)), ["DATA_GO_KR_KEY(2026-12-01)"])

    def test_health_route_and_workflow(self):
        php = (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8")
        self.assertIn("'/health'", php)
        wf = (ROOT / ".github" / "workflows" / "health.yml").read_text(encoding="utf-8")
        self.assertIn("firstlab/v1/health", wf)
        self.assertIn("sys.exit(1)", wf)


class PatchNoSwallowTest(unittest.TestCase):
    """찾을 문구가 닫는 태그로 끝나도 다음 문단을 삼키지 않음 (11/5 #51·#59·#70 다음 문단이 사라진 일)."""

    def test_find_with_closing_tag(self):
        from bot.fix_posts import patch_content
        html = "<ul><li>견적을 받아요.</li><li>항목을 맞춰 봐요.</li></ul>"
        out = patch_content(html, {"find": "견적을 받아요.</li>", "replace_paragraph": "총액 견적을 받아요.", "tag": "li"})
        self.assertEqual(out, "<ul><li>총액 견적을 받아요.</li><li>항목을 맞춰 봐요.</li></ul>")

    def test_apply_review_rejects_closing_tag(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        post = {"title": "t", "excerpt": "e", "content_html": "<p>준비하면 돼요.</p><p>다음</p>"}
        bad = [{"type": "outdated", "why": "x", "find": "준비하면 돼요.</p>", "replace_paragraph": "고쳤어요", "tag": "p"}]
        self.assertTrue(any("닫는 태그" in e for e in apply_review.check(post, bad)))


class WpMetaMissingTest(unittest.TestCase):
    """WP-CLI 는 없는 메타를 읽으면 오류로 끝남 — 처음 표시·처음 보류 횟수에서 실패하던 일 (11/5 #746 보류 수정 실패)."""

    def test_flag_first_mark_and_hold_tries(self):
        import subprocess
        from bot import changed, hold
        calls = []

        def wp(*a):
            calls.append(a)
            if a[:3] == ("post", "meta", "get"):
                raise subprocess.CalledProcessError(1, ["wp"], "", "")
            return ""
        self.assertEqual(changed.flag(["5"], "2026-11-05 x", wp), ["5"])
        with mock.patch("bot.settings.wp_cli", side_effect=wp), mock.patch.object(hold, "wp_cli", side_effect=wp):
            self.assertEqual(hold._bump_tries(7), 1)


class LeadVsBodyTest(unittest.TestCase):
    """첫 문단 단정 ↔ 본문 예외 (11/5 #748: '성인 Tdap 국가 지원 대상이 아니라' ↔ '유행할 때 임시예방접종 대상이면 국가 지원을 받을 수 있')."""

    def test_flag_and_pass(self):
        from bot import condition_gate as g
        body = ("<p>아이는 6회를 무료로 맞아요. 반대로 성인이 맞는 Tdap은 국가 지원 대상이 아니라 병원마다 금액이 달라요.</p>"
                "<h2>지원</h2><p>만 13세 이상은 백일해가 유행할 때 임시예방접종 대상이면 국가 지원을 받을 수 있어요.</p>")
        self.assertTrue(g.lead_vs_body(body))
        fixed = body.replace("국가 지원 대상이 아니라", "유행 때 임시예방접종 대상인 경우가 아니면 국가 지원이 없고")
        self.assertEqual(g.lead_vs_body(fixed), [])
        self.assertEqual(g.lead_vs_body("<p>이 카드는 연회비가 없어요.</p><p>해외 결제는 수수료가 있어요.</p>"), [])


class TickDeployTest(unittest.TestCase):
    def test_only_latest_finished_deploy_counts(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import tick
        run = lambda sha, st, c, name="Deploy server code": {"name": name, "status": st, "conclusion": c, "head_sha": sha * 7, "id": 1}  # noqa: E731
        self.assertIsNone(tick.deploy_problem([run("b", "completed", "success"), run("a", "completed", "failure")]))   # 뒤 배포가 성공
        self.assertIn("aaaaaaa", tick.deploy_problem([run("c", "in_progress", None), run("a", "completed", "failure")]))
        self.assertIsNone(tick.deploy_problem([run("h", "completed", "failure", "Health check")]))                     # 배포가 아닌 작업


class SetupScriptTest(unittest.TestCase):
    """배포 스크립트 (11/5 51e51cf 배포 실패: 권한 설정 중 SQLite 임시 파일이 사라져 chmod 실패 →
    복구가 conf.d 캐시 설정만 지워 nginx 설정 검사 실패, 디스크 설정이 깨진 채 남음)."""

    SETUP = ROOT / "server" / "setup.sh"

    def test_permissions_skip_live_sqlite_files(self):
        import os
        import subprocess
        import tempfile
        text = self.SETUP.read_text(encoding="utf-8")
        self.assertNotIn("chown -R", text)
        block = re.search(r"^LIVE=\(.*?^perm -type f[^\n]*", text, re.S | re.M).group(0)
        with tempfile.TemporaryDirectory() as d:
            db, up = Path(d, "db"), Path(d, "up")
            db.mkdir(), up.mkdir()
            Path(db, ".ht.sqlite").write_text("x"), Path(db, ".ht.sqlite-shm").write_text("x"), Path(up, "a.jpg").write_text("x")
            os.chmod(Path(db, ".ht.sqlite-shm"), 0o600)
            script = f"set -euo pipefail\nWEB_ROOT={d}\nWP_USER=$(id -un)\n{block}\necho done"
            out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(oct(os.stat(Path(up, "a.jpg")).st_mode & 0o777), "0o640")
            self.assertEqual(oct(os.stat(Path(db, ".ht.sqlite-shm")).st_mode & 0o777), "0o600")  # 사이트가 쓰는 임시 파일은 안 건드림

    def test_restore_puts_back_all_nginx_files(self):
        text = self.SETUP.read_text(encoding="utf-8")
        restore = re.search(r"^restore\(\) \{.*?^\}", text, re.S | re.M).group(0)
        installed = set(re.findall(r'install -m 644 "\$REPO_DIR/nginx/[^"]+"\s+(\S+)', text))
        files = re.search(r'^NGINX_FILES="([^"]+)"', text, re.M).group(1).split()
        self.assertEqual({f.replace("$SITE", "first-lab") for f in installed}, {f.replace("$SITE", "first-lab") for f in files})
        self.assertIn('cp -a "$BACKUP/nginx$f" "$f"', restore)       # 새로 설치한 nginx 파일은 모두 백업에서 되돌림
        self.assertNotIn("rm -f /etc/nginx/conf.d", restore)          # 한 파일만 지워 서로 안 맞는 설정을 남기지 않음
        self.assertIn("복구 실패", restore)                           # 실패를 '복구 완료'로 적지 않음


class ExcerptExclusionTest(unittest.TestCase):
    """요약 '받아요' ↔ 본문 '못 받는 경우' (11/5 #762, 글쓰기 작업 검수가 놓침)."""

    BODY = "<p>조기재취업 수당은 남은 구직급여의 절반이에요.</p><h2>못 받는 경우</h2><ul><li>월 300만 원 이상 받으면 못 받아요.</li></ul>"

    def test_flag_and_pass(self):
        from bot import condition_gate as g
        ex = "소정급여일수를 2분의 1 이상 남기면, 12개월 근무 뒤 남은 구직급여의 절반을 받아요. 조건을 정리했어요."
        self.assertTrue(g.excerpt_vs_exclusions(ex, self.BODY))
        fixed = ex.replace("절반을 받아요.", "절반을 받을 수 있어요(월 300만 원 이상 재취업 등은 제외).")
        self.assertEqual(g.excerpt_vs_exclusions(fixed, self.BODY), [])
        faq = self.BODY.replace("<h2>못 받는 경우</h2>", "<h3>복권이 찢어지면 못 받나요?</h3>")  # 묻는 제목은 제외 목록이 아님
        self.assertEqual(g.excerpt_vs_exclusions(ex, faq), [])


class LeadThresholdTest(unittest.TestCase):
    """#771 경기 극저신용대출: 요약·첫 문단 '하위 10%' ↔ 본문 '…이라면 하위 20%', 제목 '피해자' ↔ 본문 '불법사금융 피해자'."""

    BODY = ("<p>신용평점 하위 10% 이하인 도민에게 빌려줘요. 지금은 불법사금융 피해자 대출을 받아요.</p>"
            "<p>기초생활수급자·차상위계층이라면 하위 20%까지 기준이 넓어져요.</p>"
            "<p>불법사금융 피해자는 확인서가 필요해요. 불법사금융 피해자 대출은 10억 원 규모예요.</p>")

    def test_thresholds(self):
        from bot import condition_gate as g
        self.assertTrue(g.lead_thresholds("신용평점 하위 10%, 연 1%.", self.BODY))
        fixed = self.BODY.replace("하위 10% 이하인", "하위 10% 이하(기초생활수급자 등은 하위 20%)인")
        self.assertEqual(g.lead_thresholds("신용평점 하위 10%(기초생활수급자 등은 하위 20%), 연 1%.", fixed), [])
        self.assertEqual(g.lead_thresholds("", "<p>9월 1일부터 신청해요.</p><p>원하면 9월 16일부터 해요.</p>"), [])  # 날짜는 기준값 아님

    def test_title_qualifier(self):
        from bot import condition_gate as g
        self.assertTrue(g.title_qualifier("극저신용대출: 11월 30일까지 피해자 접수", self.BODY))
        self.assertEqual(g.title_qualifier("극저신용대출: 불법사금융 피해자 11월 30일까지 접수", self.BODY), [])


class CalcReviewGateTest(unittest.TestCase):
    """독립 검수 전 계산기는 공개하지 않는다 (11/5 운영자 '계산기들은 전부 발행처리했네?')."""

    def test_post_with_unreviewed_calc_is_held(self):
        import tempfile
        from bot import review_gate
        with tempfile.TemporaryDirectory() as d:
            rec = Path(d, "reviewed.txt")
            rec.write_text("# 설명\n12 어떤 글\ncalc:vat\n", encoding="utf-8")
            body = '<p>예시</p><p>[firstlab_calc id="vat"]</p><p>[firstlab_calc id="new-calc"]</p>'
            got = review_gate.unreviewed_calcs(body, rec)
            self.assertEqual(len(got), 1)
            self.assertIn("calc:new-calc", got[0])
            self.assertEqual(review_gate.unreviewed_calcs(body, Path(d, "없음.txt")), [])   # 기록 파일이 없으면 막지 않음
        self.assertTrue(any("new-calc" in p for p in review_gate.problems([{"claim": "x"}], body_html=body)))

    def test_ready_post_held_before_publish(self):
        """관문 뒤 대기 중인 글도 공개 직전에 다시 (11/5 school-grade: 규칙 전에 대기열에 들어감)."""
        import datetime as dt
        from bot import freshness, review_gate

        def wp(*a):
            if a[:2] == ("post", "get") and a[3] == "--field=post_content":
                return '<p>[firstlab_calc id="not-reviewed-yet"]</p>'
            return "" if a[:3] == ("post", "meta", "get") else "2026-11-01"
        with mock.patch.object(freshness, "wp_cli", side_effect=wp), mock.patch.object(freshness, "problems", side_effect=lambda *a: []), \
                mock.patch.object(freshness, "_review_from_queue", return_value=[{"claim": "c"}]):
            got = freshness.check_post(1, dt.date(2026, 11, 6))
        self.assertTrue(any("not-reviewed-yet" in g for g in got), got)
        self.assertTrue(review_gate.reviewed_calcs())   # 저장소 검수 기록을 실제로 읽음

    def test_calc_pages_published_only_when_reviewed(self):
        setup = (ROOT / "server" / "wp-setup.sh").read_text(encoding="utf-8")
        self.assertIn('grep -qE "^calc:${slug%-calculator}', setup)
        self.assertIn('--post_status="$status"', setup)
        self.assertNotIn("--post_status=publish", setup)
        # 실제 판정: 검수 기록이 있는 계산기만 publish, 계산기 아닌 페이지는 늘 publish
        import subprocess
        import tempfile
        cond = re.search(r"^  status=publish\n.*?^  fi$", setup, re.S | re.M).group(0)
        with tempfile.TemporaryDirectory() as d:
            Path(d, "automation").mkdir()
            Path(d, "automation", "reviewed.txt").write_text("calc:vat\ncalc:vat-extra 메모\n", encoding="utf-8")
            for slug, want in (("vat-calculator", "publish"), ("new-calculator", "draft"), ("vat-ext-calculator", "draft"), ("about", "publish")):
                out = subprocess.run(["bash", "-c", f'REPO_DIR={d}; slug={slug}\n{cond}\necho $status'], capture_output=True, text=True)
                self.assertEqual(out.stdout.strip(), want, slug)


class PageIdsTest(unittest.TestCase):
    """초안 페이지를 못 찾아 '-2' 페이지를 또 만든 일 (11/5 school-grade-calculator-2)."""

    def test_pick_keeps_one_and_finds_drafts(self):
        sys.path.insert(0, str(ROOT / "server"))
        import page_ids
        rows = ("ID,post_name,post_status\n780,school-grade-calculator,draft\n787,school-grade-calculator-2,publish\n"
                "5,about,publish\n9,vat-calculator,publish\n11,vat-calculator-2,publish\n")
        self.assertEqual(page_ids.pick(rows, "school-grade-calculator"), ("787", ["780"]))   # 공개된 것을 남기고 주소는 원래대로
        self.assertEqual(page_ids.pick("ID,post_name,post_status\n780,school-grade-calculator,draft\n", "school-grade-calculator"), ("780", []))
        self.assertEqual(page_ids.pick(rows, "about"), ("5", []))
        self.assertEqual(page_ids.pick(rows, "vat-calculator", {"vat-calculator", "vat-calculator-2"}), ("9", []))  # 다른 저장소 페이지는 안 건드림
        self.assertEqual(page_ids.pick(rows, "new-calculator"), ("", []))
        setup = (ROOT / "server" / "wp-setup.sh").read_text(encoding="utf-8")
        self.assertNotIn('--name="$slug"', setup)
        self.assertIn('--post_name="$slug" --post_title', setup)
        self.assertNotIn("delete \"$x\" --force", setup)   # 중복은 휴지통으로만

    def test_shell_reads_ids(self):
        import subprocess
        out = subprocess.run(["bash", "-c", 'csv=$(printf "ID,post_name,post_status\\n1,a,draft\\n2,a-2,publish\\n"); '
                              f'read -r id extras <<<"$(printf \'%s\\n\' "$csv" | python3 {ROOT}/server/page_ids.py a a b)"; echo "$id|$extras"'],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "2|1")


class CitedUrlsTest(unittest.TestCase):
    def test_reviewer_gets_writer_sources(self):
        """검수 파일에 글쓴이 근거 주소 + 지시문이 '확인불가 전에 열기' (11/5 #775 고시를 안 열어 확인불가 19건)."""
        sys.path.insert(0, str(ROOT / "automation"))
        import unreviewed
        post = {"source_check": {"found": [{"urls": ["https://a.go.kr", "https://law.go.kr/x"]}], "claims": [{"urls": ["https://a.go.kr"]}]}}
        self.assertEqual(unreviewed.cited_urls(post), ["https://a.go.kr", "https://law.go.kr/x"])
        self.assertEqual(unreviewed.cited_urls({}), [])
        self.assertIn("cited_urls", (ROOT / "automation" / "review-prompt.md").read_text(encoding="utf-8"))
        prompt = (ROOT / "automation" / "review-prompt.md").read_text(encoding="utf-8")
        for need in ("independent implementation", "pdftotext", "every label"):   # 계산기 검수 깊이 (11/5 #784 글쓰기 검수 틀림 0 → 외부 6건)
            self.assertIn(need, prompt)
        self.assertIn("review-prompt.md` 의 계산기 항목", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))


class LeadMinimumTest(unittest.TestCase):
    """#775 혼례비 대출: 첫 문단 '둘 다 3개월 이상 일하고 있어야' ↔ 본문 '일용근로자는 … 45일 이상이면'."""

    BODY = ("<p>직접 융자는 연 1.5%예요. 둘 다 3개월 이상 일하고 있어야 하고, 3년 이내에 신청해요.</p>"
            "<ul><li>사업장에서 3개월 이상 일하고 있는 근로자</li>"
            "<li>일용근로자는 신청일 이전 90일 안에 근로일수가 45일 이상이면 돼요</li></ul>")

    def test_flag_and_pass(self):
        from bot import condition_gate as g
        self.assertTrue(g.lead_minimums("", self.BODY))
        fixed = self.BODY.replace("둘 다 3개월 이상 일하고", "둘 다 원칙적으로 3개월 이상 일하고(일용근로자는 최근 90일 중 45일 이상)")
        self.assertEqual(g.lead_minimums("", fixed), [])
        self.assertEqual(g.lead_minimums("", "<p>3개월 이상 일해야 해요.</p><p>신청은 50만 원 이상이에요.</p>"), [])


class VariantOnlyTest(unittest.TestCase):
    """#784: 5등급제·9등급제를 나눠 설명하는 글에서 갈래 없이 '…만 적어요'."""

    BODY = ("<h2>5등급제 비율</h2><p>1등급은 10%예요.</p><h2>9등급제 비율</h2><p>1등급은 4%예요.</p>"
            "<h2>석차등급이 없는 과목</h2><ul><li>융합 선택 9과목은 성취도만 적고 석차등급은 '·'예요.</li></ul>")

    def test_flag_and_pass(self):
        from bot import condition_gate as g
        self.assertTrue(g.variant_only(self.BODY))
        self.assertEqual(g.variant_only(self.BODY.replace("융합 선택", "5등급제에서 융합 선택")), [])
        self.assertEqual(g.variant_only(self.BODY.replace("석차등급이 없는 과목", "5등급제에서 석차등급이 없는 과목")), [])
        self.assertEqual(g.variant_only("<h2>신청</h2><p>본인만 신청할 수 있어요. 서류만 내면 돼요.</p>"), [])   # 갈래가 없는 글


class LawAbsenceTest(unittest.TestCase):
    def test_flag_and_pass(self):
        """#690 \'파킹통장은 법에 있는 상품 이름이 아니에요\' — 확인 못 한 부정 단정."""
        from bot import condition_gate as g
        self.assertTrue(g.law_absence("<p>'파킹통장'은 법에 있는 상품 이름이 아니에요. 금감원에서 볼 수 있어요.</p>"))
        self.assertEqual(g.law_absence("<p>'파킹통장'은 이런 상품을 부르는 별칭이에요.</p><p>법에 따라 1억 원까지 보호돼요.</p>"), [])


class TransitionRuleTest(unittest.TestCase):
    """부칙 경과조치: 개정으로 바뀐 숫자는 적용 시점과 함께 (11/5 #762 하루 상한 68,100원 ← 2025년 이전 퇴사자는 66,000원)."""

    PAGE = ("부      칙     <대통령령 제35934호 2025. 12. 23.>  제1조(시행일)  이 영은 2026년 1월 1일부터 시행한다. "
            "제4조(급여기초 임금일액 및 구직급여일액의 상한액에 관한 경과조치)  이 영 시행 전에 이직한 사람의 구직급여의 산정 기초가 되는 "
            "임금일액 및 구직급여일액은 제68조제1항 제104조의8제4항의 개정규정에도 불구하고 종전의 규정에 따른다. "
            "제5조(가산세에 관한 경과조치)  이 영 시행 전에 공급한 경우에는 제60조제1항제2호의 개정규정에도 불구하고 종전의 규정에 따른다. "
            "제14조(납세조합 세액공제에 관한 경과조치)  이 영 시행 전에 발생한 소득은 제150조제3항의 개정규정에도 불구하고 종전의 규정에 따른다. ")
    URL = "https://www.law.go.kr/법령/고용보험법시행령/제68조"
    REVIEW = [{"claim": "하루 상한 68,100원 (기초일액 상한 11만3500원 × 60%) (고용보험법 시행령 제68조)", "verdict": "맞음",
               "action": "유지", "urls": [URL]}]
    BODY = "<p>구직급여일액은 평균임금의 60%인데, 하루 상한은 68,100원이에요.</p>"

    def run_gate(self, body, review=None, changed=frozenset({"11만3500"}), today=None):
        import datetime as dt
        from bot import condition_gate as g
        return g.transition_rules(review or self.REVIEW, body, page_of=lambda b: self.PAGE,
                                  changed_of=lambda b, a, d: set(changed), today=today or dt.date(2026, 11, 5))

    def test_flag_and_pass(self):
        self.assertTrue(self.run_gate(self.BODY))
        fixed = self.BODY.replace("하루 상한은", "2026년 1월 1일 이후 퇴사했다면 하루 상한은").replace("이에요", "(그 전 퇴사는 종전 66,000원)이에요")
        self.assertEqual(self.run_gate(fixed), [])

    def test_no_false_alarm(self):
        import datetime as dt
        self.assertEqual(self.run_gate(self.BODY, changed=set()), [])                     # 그 개정이 주장 숫자를 안 바꿈
        self.assertEqual(self.run_gate(self.BODY, today=dt.date(2028, 6, 1)), [])         # 오래된 개정
        for art in ("제60조", "제14조"):                                                    # 호까지 짚은 경과조치 / 부칙 조 제목
            review = [dict(self.REVIEW[0], claim=f"공급가액 1,000,000원 (부가가치세법 {art})", urls=[f"https://www.law.go.kr/법령/고용보험법시행령/{art}"])]
            self.assertEqual(self.run_gate("<p>공급가액 1,000,000원이에요.</p>", review=review, changed={"1000000"}), [])

    def test_offline_never_blocks(self):
        from bot import condition_gate as g, law_api
        with mock.patch.object(law_api, "available", return_value=False):
            self.assertEqual(g.transition_rules(self.REVIEW, self.BODY), [])


class BokApiTest(unittest.TestCase):
    def test_base_rate_latest_and_no_key(self):
        import datetime as dt
        from bot import bok_api
        rows = {"StatisticSearch": {"row": [{"TIME": "20260101", "DATA_VALUE": "2.75"}, {"TIME": "20261015", "DATA_VALUE": "2.5"}]}}
        with mock.patch.object(bok_api, "_key", return_value="k"), \
                mock.patch("urllib.request.urlopen", return_value=mock.MagicMock(__enter__=lambda s: s, __exit__=lambda *a: False,
                                                                                  read=lambda: json.dumps(rows).encode())):
            self.assertEqual(bok_api.base_rate(dt.date(2026, 11, 5)), ("20261015", 2.5))
        with mock.patch.object(bok_api, "_key", return_value=""):
            self.assertIsNone(bok_api.base_rate(dt.date(2026, 11, 5)))   # 키 없으면 영향 없음


class RatesTest(unittest.TestCase):
    """한국은행 금리: 매일 저장, 기준금리가 바뀌면 '기준금리'가 든 공개 글·초안에 재검증 표시."""

    def test_base_rate_change_marks_published_posts(self):
        import datetime as dt
        from bot import bok_api, law_watch, wpbulk
        rates = {"base": {"date": "20261127", "value": 2.75}, "mortgage": {"month": "202610", "value": 4.1, "name": "주택담보대출"}}
        state = {"rates": {"base": {"date": "20261103", "value": 3.0}}, "log": []}
        bulk = {"10": {"status": "publish", "content": "기준금리 3.0% 기준 예시"}, "11": {"status": "draft", "content": "기준금리"},
                "12": {"status": "publish", "content": "전세 대출 이자"}}
        calls = []
        with mock.patch.object(bok_api, "_key", return_value="k"), mock.patch.object(bok_api, "latest_rates", return_value=rates), \
                mock.patch("bot.state.load_state", return_value=state), mock.patch("bot.state.save_state") as save, \
                mock.patch.object(wpbulk, "posts", return_value=bulk), \
                mock.patch("bot.settings.wp_cli", side_effect=lambda *a: calls.append(a) or ""):
            msg = bok_api.run(dt.date(2026, 11, 28))
        updates = [c for c in calls if c[2] == "update"]
        self.assertEqual([c[3] for c in updates], ["10", "11"])  # '기준금리'가 든 공개 글·초안 (초안은 공개 직전 보류, freshness)
        self.assertEqual(updates[0][4], law_watch.LAW_META)
        self.assertIn("3.0%→2.75%", updates[0][5])
        self.assertIn("재검증 표시 2편", msg)
        self.assertEqual(save.call_args.args[0]["rates"]["base"]["value"], 2.75)
        with mock.patch.object(bok_api, "_key", return_value=""):
            self.assertIn("키 없음", bok_api.run(dt.date(2026, 11, 28)))


class SmallWorkplaceGateTest(unittest.TestCase):
    """11/5 #613·#630: 근로기준법 가산수당·연차는 4명 이하 미적용인데 요약에 조건이 두 번 빠짐 → 관문이 막음."""

    def test_requires_condition_in_headline(self):
        from bot.review_gate import _small_workplace
        review = [{"claim": "연차는 1년 80% 출근 시 15일 (근로기준법 제60조)"}]
        self.assertTrue(_small_workplace(review, "연차 개수 계산기: 1년 15일 / 연차는 1년 80% 출근하면 15일이에요."))
        self.assertEqual(_small_workplace(review, "연차 계산기 / 상시 5명 이상 사업장에서 연차는 15일"), [])
        self.assertEqual(_small_workplace(review, "연차 계산기 / 5인 이상 회사 기준"), [])
        self.assertEqual(_small_workplace([{"claim": "주휴수당 (근로기준법 제55조)"}], "주휴수당 계산"), [])  # 제55조는 4명 이하도 적용
        self.assertEqual(_small_workplace([{"claim": "근로기준법 제60조의2"}], "x"), [])


class EarlyTopicsTest(unittest.TestCase):
    """한발 빠른 글감 (11/5 운영자): 생활 핵심 법령의 시행 예정 개정(바뀌는 조문까지), 해마다 나오는 기준값 고시."""

    def test_core_changes_lists_changed_articles_only(self):
        import datetime as dt
        from types import SimpleNamespace as NS
        from bot import law_api, law_watch
        d = dt.date
        vers = [{"mst": "1", "date": d(2026, 9, 1)}, {"mst": "2", "date": d(2026, 12, 10)}, {"mst": "3", "date": d(2027, 1, 1)}]
        arts = {"1": {"제54조": "휴게 30분", "제55조": "주휴"}, "2": {"제54조": "휴게 1시간", "제55조": "주휴"},
                "3": {"제54조": "휴게 1시간", "제55조": "주휴 <개정 2026.12.1>"}}
        api = NS(versions=lambda n: vers, current=law_api.current, all_articles=lambda v: arts[v["mst"]],
                 changed_articles=law_api.changed_articles)
        with mock.patch.object(law_watch, "CORE_LAWS", ("근로기준법",)):
            got = law_watch.core_changes(d(2026, 11, 5), api=api)
        self.assertEqual([(g["date"], g["articles"]) for g in got], [("2026-12-10", ["제54조"])])  # 1/1 판은 '<개정>' 표시만 → 뺌

    def test_notice_changes(self):
        import datetime as dt
        from bot import law_watch
        xml = ("<AdmRulSearch>"
               "<admrul id='1'><행정규칙명>2027년 적용 최저임금 고시</행정규칙명><발령일자>20260805</발령일자><시행일자>20270101</시행일자><소관부처명>고용노동부</소관부처명><행정규칙일련번호>11</행정규칙일련번호></admrul>"
               "<admrul id='2'><행정규칙명>2027년 적용 최저임금안 고시</행정규칙명><발령일자>20260716</발령일자><시행일자>20270101</시행일자><소관부처명>고용노동부</소관부처명><행정규칙일련번호>12</행정규칙일련번호></admrul>"
               "<admrul id='3'><행정규칙명>2025년 적용 최저임금 고시</행정규칙명><발령일자>20240805</발령일자><시행일자>20250101</시행일자><소관부처명>고용노동부</소관부처명><행정규칙일련번호>13</행정규칙일련번호></admrul>"
               "</AdmRulSearch>")
        with mock.patch.object(law_watch, "NOTICE_QUERIES", (("최저임금", "고용노동부"),)):
            got = law_watch.notice_changes(dt.date(2026, 11, 5), fetch=lambda *a, **k: xml)
        self.assertEqual([g["key"] for g in got], ["고시|11"])   # 확정 전 '안 고시'·오래된 고시는 뺌
        self.assertEqual(got[0]["date"], "2027-01-01")

    def test_skip_file_marks_law_change_and_benefit_done(self):
        from bot import publish_queue
        state = {"law_changes_done": ["a"], "new_benefits_done": []}
        with mock.patch.object(publish_queue, "connect"), mock.patch.object(publish_queue, "load_state", return_value=state), \
                mock.patch.object(publish_queue, "save_state") as save:
            msg = publish_queue.skip_keywords({"type": "skip", "keywords": [], "law_changes": ["근로기준법|2026-12-08"], "new_benefits": ["408"]})
        saved = save.call_args.args[0]
        self.assertEqual(saved["law_changes_done"], ["a", "근로기준법|2026-12-08"])
        self.assertEqual(saved["new_benefits_done"], ["408"])
        self.assertIn("건너뛴 제도·혜택 글감 2개", msg)


class BenefitWatchTest(unittest.TestCase):
    """새로 생긴 혜택 (11/5 운영자: 검색량 없는 새 정책도 먼저 쓰기) — 전국·광역, 개인·가구·소상공인, 안 쓴 것만."""

    def test_pick_new_benefits(self):
        from bot import benefit_watch
        rows = [{"서비스ID": "1", "서비스명": "고속도로 다자녀가구 통행료 할인", "소관기관유형": "공공기관", "사용자구분": "가구", "등록일시": "20260814"},
                {"서비스ID": "2", "서비스명": "안성시 출산비용", "소관기관유형": "시군구", "사용자구분": "개인", "등록일시": "20261008"},
                {"서비스ID": "3", "서비스명": "충북 평생교육이용권", "소관기관유형": "광역시도", "사용자구분": "개인", "소관기관명": "충청북도", "등록일시": "20261002"},
                {"서비스ID": "4", "서비스명": "법인 지원", "소관기관유형": "중앙행정기관", "사용자구분": "법인/시설/단체", "등록일시": "20261001"},
                {"서비스ID": "5", "서비스명": "이미 쓴 것", "소관기관유형": "중앙행정기관", "사용자구분": "개인", "등록일시": "20261003"}]
        rows.append({"서비스ID": "6", "서비스명": "마감된 것", "소관기관유형": "중앙행정기관", "사용자구분": "개인", "등록일시": "20261009",
                     "신청기한": "2026.10.12~2026.10.30"})
        got = benefit_watch.pick(rows, done={"5"}, today=__import__("datetime").date(2026, 11, 5))
        self.assertEqual([g["key"] for g in got], ["1", "3"])          # 시군구·법인 대상·이미 쓴 것·신청 마감 제외, 전국 먼저
        self.assertFalse(benefit_watch.apply_closed("상시신청", __import__("datetime").date(2026, 11, 5)))
        self.assertEqual(got[1]["scope"], "충청북도 한정")              # 광역은 지역을 밝혀 쓰게
        self.assertEqual(got[0]["scope"], "전국")


class BenefitApiTest(unittest.TestCase):
    """혜택 정보 API: 키는 서버에만, 인코딩된 키·원래 키 모두 처리, 통로는 허용한 경로·변수만."""

    def test_key_forms_and_proxy(self):
        from bot import benefit_api
        raw = benefit_api._direct_url("serviceList", "a+b/c==", {"page": 1})
        self.assertTrue(raw.endswith("&serviceKey=a%2Bb%2Fc%3D%3D"))
        self.assertTrue(benefit_api._direct_url("serviceList", "a%2Bb", {}).endswith("&serviceKey=a%2Bb"))  # 이미 인코딩된 키는 그대로
        with mock.patch.object(benefit_api, "_server_key", return_value=""), \
                mock.patch.dict("os.environ", {"FIRSTLAB_API_TOKEN": "t"}), \
                mock.patch.object(benefit_api, "_get", return_value=json.dumps({"status": 200, "body": '{"data": [1]}'})):
            self.assertEqual(benefit_api.fetch("serviceList", perPage=1), {"data": [1]})
        with mock.patch.object(benefit_api, "_server_key", return_value=""), mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(benefit_api.fetch("serviceList"))

    def test_site_route_whitelist(self):
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        code = ("define('ABSPATH',1); function add_action(){} require '" + str(ROOT / "wordpress/mu-plugins/first-lab-api.php") + "';"
                "echo json_encode([firstlab_benefit_url(['path'=>'serviceDetail','cond'=>['서비스ID::EQ'=>'X1'],'perPage'=>'5','evil'=>'1'],'k+1'),"
                "firstlab_benefit_url(['path'=>'../x'],'k'), firstlab_benefit_url(['path'=>'serviceList'],'k%2B1')]);")
        ok, bad, enc = json.loads(subprocess.run(["php", "-r", code], capture_output=True, text=True, check=True).stdout)
        self.assertTrue(ok.startswith("https://api.odcloud.kr/api/gov24/v3/serviceDetail?returnType=JSON"))
        self.assertIn("perPage=5", ok)
        self.assertIn(urllib.parse.quote("cond[서비스ID::EQ]", safe="") + "=X1", ok)
        self.assertNotIn("evil", ok)
        self.assertTrue(ok.endswith("&serviceKey=k%2B1"))
        self.assertIsNone(bad)
        self.assertTrue(enc.endswith("&serviceKey=k%2B1"))


if __name__ == "__main__":
    unittest.main()
