"""배포 전 자동 검사 (GitHub Actions). 서버·API 없이 도는 순수 로직만 확인한다.
실행: python -m unittest discover -s tests -v
"""
import json
import re
import sqlite3
import sys
import importlib.util
import tempfile
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
    BODY = "<p>수수료는 23,000원이에요. 수수료는 23,000원이에요.</p>" * 10  # 문단 300자 기준(읽기) 안쪽

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
    def run_step(self, state, minute, on=True, fire_ok=True, writers=1, writer2=True):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot import run
        now = datetime(2026, 11, 5, 10, minute, tzinfo=ZoneInfo("Asia/Seoul"))
        settings = BotSettings.from_option({}, {})
        with mock.patch.object(run, "WRITERS", writers), mock.patch.object(run, "continuous_on", return_value=on), \
                mock.patch.object(run, "writer2_on", return_value=writer2), mock.patch.object(run, "set_continuous") as stop, \
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

    def test_two_writers_tracked_per_slot(self):
        """11/6 운영자 '둘 다 해 봐': 글쓰기 2개 동시 — 칸마다 'slot-N' 으로 시작, 끝 표시 '[N번]' 으로 칸별로 다음 편."""
        from bot import run
        self.assertEqual(run.WRITERS, 2)
        state = {"log": []}
        fire, _ = self.run_step(state, 0, writers=2)
        self.assertEqual([c.args[3] for c in fire.call_args_list], ["slot-1", "slot-2"])     # 두 칸 모두 시작
        state["log"].insert(0, {"time": "2026-11-05 10:15", "ok": True, "msg": "글쓰기 실행 끝(직접): [2번] 새 글 x"})
        fire, _ = self.run_step(state, 16, writers=2)
        self.assertEqual([c.args[3] for c in fire.call_args_list], ["slot-2"])               # 끝난 2번 칸만 다음 편
        state["log"].insert(0, {"time": "2026-11-05 10:20", "ok": True, "msg": "글쓰기 실행 끝(직접): 보류 수정"})  # 칸 표시 없음 = 1번
        fire, _ = self.run_step(state, 21, writers=2)
        self.assertEqual([c.args[3] for c in fire.call_args_list], ["slot-1"])
        old = {"log": [{"time": "2026-11-05 09:50", "ok": True, "msg": "글쓰기 실행 끝(직접): 예전"}],
               "continuous": {"fired_at": "2026-11-05 10:00", "fails": 0, "count": 5, "seen": "2026-11-05 09:40 직접 작성 글: x"}}
        fire, _ = self.run_step(old, 5, writers=2)                                          # 배포 직후: 1번은 아직 쓰는 중
        self.assertEqual([c.args[3] for c in fire.call_args_list], ["slot-2"])

    def test_writer2_off_runs_slot1_only(self):
        """11/7 운영자 '설정에 2번 작성자 온오프 버튼': 끄면 1번 칸만 시작, 2번 칸 기록은 지움."""
        state = {"log": [], "continuous_2": {"fired_at": "2026-11-05 09:50", "fails": 0, "count": 3, "seen": ""}}
        fire, _ = self.run_step(state, 0, writers=2, writer2=False)
        self.assertEqual([c.args[3] for c in fire.call_args_list], ["slot-1"])
        self.assertNotIn("continuous_2", state)
        api = (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8")
        self.assertIn("firstlab_writer2", api)            # 1번 칸이 후보 전부를 받음
        page = (ROOT / "wordpress" / "mu-plugins" / "first-lab-bot-settings.php").read_text(encoding="utf-8")
        self.assertIn("firstlab_bot_writer2", page)       # 설정 페이지 버튼

    def test_waits_for_fresh_candidates(self):
        """11/6 09:45: 끝 표시와 같은 배포의 후보 제외가 후보 목록에 반영되기 전에 다음 편이 시작돼 옛 후보(방금 뺀 것)를 받음."""
        state = {"log": [], "dashboard_at": ""}
        fire, _ = self.run_step(state, 0)
        self.assertEqual(fire.call_count, 0)                  # 후보 새로 만들기 전 → 이번 분은 기다림
        state["dashboard_at"] = "2026-11-05 10:00"
        fire, _ = self.run_step(state, 1)
        self.assertEqual(fire.call_count, 1)

    def test_more_candidates_info_first_within_gold(self):
        from bot import run
        self.assertEqual(run.CANDIDATES, 40)
        self.assertEqual(run.CANDIDATES_POOL, 120)                                       # 다음 쪽(?page=2·3)용
        api = (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8")
        self.assertIn("'candidates_more'", api)
        self.assertIn("?page=2", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))
        self.assertIn("( $page - 1 ) * 40, 40 )", (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8"))
        self.assertIn('x["info"], x["score"])', (ROOT / "bot" / "select_keyword.py").read_text(encoding="utf-8"))   # 황금 안에서 정보 검색 먼저

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

    def test_nested_list_kept_whole(self):
        """11/6 #872: 목록 안 목록을 '.*?</ul>' 로 끊어 '</li></ul>' 이 빈 문단이 되고 항목이 밖으로 빠짐."""
        import re
        from bot.blocks import to_blocks
        src = "<ul>\n<li>나이</li>\n<li>위기: 다음 중\n<ul>\n<li>A</li>\n<li>B</li>\n</ul></li>\n</ul>\n<p>뒤</p>"
        out = to_blocks(src)
        self.assertNotRegex(out, r"<p>\s*</li>")
        self.assertEqual(out.count("<!-- wp:list -->"), 2)                      # 바깥·안쪽 목록 블록
        self.assertIn("<li>위기: 다음 중\n<!-- wp:list -->", out)              # 안쪽 목록이 그 항목 안에
        strip = lambda h: re.sub(r"\s+", "", re.sub(r"<!-- /?wp:[^>]*-->|\sclass=\"[^\"]*\"", "", h))  # noqa: E731
        self.assertEqual(strip(out), strip(src))                                # 글자·구조 그대로

    def test_repair_broken_nested_list(self):
        """이미 깨진 채 저장된 글(옛 변환 결과)은 배포 보정에서 되살림. 짝이 안 맞으면 손대지 않음."""
        from bot.blocks import repair_lists, to_blocks
        src = "<p>앞</p>\n<ul>\n<li>x</li>\n<li>위기\n<ul>\n<li>A</li>\n<li>B</li>\n</ul></li>\n</ul>\n<p>뒤</p>"
        broken = ("<!-- wp:paragraph -->\n<p>앞</p>\n<!-- /wp:paragraph -->\n\n<!-- wp:list -->\n<ul class=\"wp-block-list\">\n"
                  "<!-- wp:list-item -->\n<li>x</li>\n<!-- /wp:list-item -->\n<!-- wp:list-item -->\n<li>위기\n<ul>\n<li>A</li>\n"
                  "<!-- /wp:list-item -->\n<!-- wp:list-item -->\n<li>B</li>\n<!-- /wp:list-item -->\n</ul>\n<!-- /wp:list -->\n\n"
                  "<!-- wp:paragraph -->\n<p></li>\n</ul></p>\n<!-- /wp:paragraph -->\n\n<!-- wp:paragraph -->\n<p>뒤</p>\n<!-- /wp:paragraph -->")
        fixed, n = repair_lists(broken)
        self.assertEqual(n, 1)
        self.assertEqual(fixed.strip(), to_blocks(src).strip())
        self.assertEqual(repair_lists(fixed), (fixed, 0))                       # 다시 돌려도 그대로
        odd = broken.replace("<p></li>\n</ul></p>", "<p></li></p>")            # 짝이 끝내 안 맞음
        self.assertEqual(repair_lists(odd)[1], 0)

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
        self.assertIn("_firstlab_related_ids", php)   # 내용 기준 기록만 씀 (주휴수당 뜻 ↔ 계산) — 동작은 ContentRelatedTest

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
        self.assertTrue(is_official("https://en-ter.co.kr/support/info/info01/info.do"))  # 한전 에너지마켓플레이스 (11/6 #980)
        self.assertTrue(is_official("https://www.mnuri.kr/useOfCard/onlineMerchants.do"))  # 문화누리카드 (11/7 #1262)
        self.assertFalse(is_official("https://fake-en-ter.co.kr/x"))
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


class KeywordCollectFailureTest(unittest.TestCase):
    """11/9 운영자 '7일부터 안 됐는데 왜 이제 말하나': 검색광고 API 403 이 '새 키워드 0개 수집 성공'으로 기록돼
    봇 기록·2시간 점검 어디에도 ❌ 가 안 떴음 → 키 거부는 실패로, 마지막 성공 날짜를 남기고 점검이 이틀 넘으면 문제로."""

    def test_auth_error_fails_the_run(self):
        import io
        import sqlite3
        import urllib.error
        from contextlib import contextmanager
        from bot import discover

        class Fake:
            def keyword_stats(self, batch):
                raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b'{"detail":"Invalid"}'))

        @contextmanager
        def conn():
            c = sqlite3.connect(":memory:")
            c.row_factory = sqlite3.Row
            try:
                yield c
            finally:
                c.close()

        with mock.patch.object(discover, "ads_client", return_value=Fake()), \
                mock.patch.object(discover, "connect", conn), mock.patch.object(discover, "google_trends_kr", return_value=[]), \
                mock.patch.object(discover, "focus_snowball", return_value=[]), mock.patch.object(discover.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "검색광고 API.*403"):
                discover.discover(5)

    def test_last_success_recorded_and_stale_flagged(self):
        from datetime import datetime
        from bot import discover
        sys.path.insert(0, str(ROOT / "automation"))
        from tick import keyword_line
        state = {}
        discover.record_collect(state, True, "ok", datetime(2026, 11, 6, 4))
        with mock.patch("bot.naver_ads.diagnose", return_value={"ok": False, "status": 403, "message": "Invalid"}):
            discover.record_collect(state, False, "키워드 수집 실패: 네이버 검색광고 API 가 키를 거부함", datetime(2026, 11, 9, 4))
        self.assertEqual(state["keyword_collect"]["ok_date"], "2026-11-06")
        self.assertEqual(state["naver_ads_check"]["status"], 403)
        _, bad = keyword_line(state["keyword_collect"], state["naver_ads_check"], datetime(2026, 11, 9, 12))
        self.assertIn("검색광고 API 실패", bad)
        _, bad = keyword_line(state["keyword_collect"], {"ok": True}, datetime(2026, 11, 9, 12))
        self.assertIn("2일 넘게", bad)
        _, bad = keyword_line({"ok_date": "2026-11-08"}, {"ok": True}, datetime(2026, 11, 9, 12))
        self.assertIsNone(bad)


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
        body = ("<p>" + "가" * 70 + ".</p>") * 30  # 공백 뺀 2,100자 → 30개 (문장 100자·문단 300자 기준 안쪽)
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

    def test_quota_does_not_count_as_failed_round(self):
        """11/7 #1042·#1030·#1006: 한도 소진 2번을 '2번 실패'로 세어 '사진을 2번 못 넣음 → 제목 카드' 로 영영 포기."""
        from bot import image_check, images, photo
        calls = []

        def fake_cli(*args):
            calls.append(args)
            if args[:3] == ("post", "meta", "get"):
                return {image_check.ROUND_META: "1"}.get(args[4], "")
            return ""
        with mock.patch.object(image_check, "wp_cli", side_effect=fake_cli), \
             mock.patch.object(photo, "wp_cli", side_effect=fake_cli), \
             mock.patch.object(image_check, "load_settings", return_value=mock.Mock(cf_account_id="a", cf_api_token="t")), \
             mock.patch.object(photo, "make", side_effect=images.QuotaExceeded("한도")):
            image_check.apply_check({"type": "image_check", "post_id": 1042, "add": [{"prompt": "a parking garage", "alt": "주차장"}]})
        rounds = [c[5] for c in calls if c[:3] == ("post", "meta", "update") and c[4] == image_check.ROUND_META]
        self.assertEqual(rounds[-1], "1")   # 되돌림 (올리지 않음)

    def test_no_give_up_while_quota_blocked(self):
        from bot import images
        with mock.patch.object(images, "quota_blocked", return_value=True):
            msg, calls, made, card = self.run_check({"replace": [], "remove": [], "add": []}, current="", rounds="2")
        self.assertFalse(any("_firstlab_img_checked" in c for c in calls))
        self.assertIn("한도 소진", msg)

    def test_text_prone_word_removed_instead_of_dropping_scene(self):
        """11/7 #1042 공항 주차장: 장면 4개가 다 'airport' 때문에 버려져 사진을 영영 못 넣음 → 그 말만 빼고 씀."""
        from bot import images, photo
        self.assertEqual(images.safe_scene("a quiet covered airport parking deck with neatly parked cars"),
                         "a quiet covered parking deck with neatly parked cars")
        self.assertEqual(images.safe_scene("a printed contract"), "")
        add = [{"prompt": "firefighter helmets hanging on hooks inside a fire station equipment room", "alt": "소방 헬멧"}]
        msg, calls, made, card = self.run_check({"add": add}, current="")
        self.assertEqual(made[0]["prompt"], "firefighter helmets hanging on hooks inside a fire equipment room")

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
        self.assertTrue(sc.has_number("인터넷 www.15990903.or.kr 예약", "15990903"))   # 11/7 #1462 주소 속 숫자
        self.assertFalse(sc.has_number("요율 3.5%", "5"))                                 # 소수점 뒤는 아님
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


class BenefitDetailTest(unittest.TestCase):
    """정부24 혜택 원문(공공서비스 API 상세)으로 혜택 글 대조 (11/6 운영자 '가진 API 다 써서 더 정확하게' 1번).
    #872: 문의처 번호가 원문(성평등가족부 02-2100-6313)과 달랐는데 근거 대조는 화면만 봤음."""

    DETAIL = {"서비스명": "위기청소년 특별 지원", "문의처": "성평등가족부/02-2100-6313", "지원내용": "생활지원 월 65만원 이하",
              "공무원확인구비서류": "해당없음", "온라인신청사이트URL": None}

    def test_service_ids_from_gov24_urls(self):
        from bot import benefit_detail as bd
        urls = ["https://www.gov.kr/portal/rcvfvrSvc/dtlEx/WII000001600", "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/WII000001600?x=1",
                "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/627000000759", "https://x.go.kr/a?svcId=ABC1234567", "https://www.law.go.kr/법령/a"]
        self.assertEqual(bd.service_ids(urls), ["WII000001600", "627000000759", "ABC1234567"])

    def test_text_has_fields_and_age(self):
        from bot import benefit_detail as bd
        calls = []

        def fetch(path, **kw):
            calls.append((path, kw))
            return {"data": [self.DETAIL if path == "serviceDetail" else {"JA0110": 9, "JA0111": 24, "JA0101": "Y"}]}
        text = bd.detail_text("WII000001600", fetch=fetch)
        self.assertIn("문의처: 성평등가족부/02-2100-6313", text)
        self.assertIn("대상 나이: 9세~24세", text)
        self.assertNotIn("해당없음", text)                        # 비어 있는 항목은 뺌
        self.assertEqual(calls[0], ("serviceDetail", {"cond[서비스ID::EQ]": "WII000001600"}))
        self.assertIsNone(bd.detail_text("X", fetch=lambda path, **kw: {"data": []}))

    def test_delegated_rule_must_be_read(self):
        """11/6 #885(시행령에만 있는 반환 청구 기간)·#896(고시에만 있는 적용 범위) — 같은 꼴 2번 → 위임 확인 검사."""
        from bot import source_check as sc
        law = "https://www.law.go.kr/법령/채용절차의공정화에관한법률"
        text = "제11조(채용서류의 반환 등) ① 반환하여야 한다. ④ 구인자는 대통령령으로 정한 반환의 청구기간이 지난 경우 파기하여야 한다. 제12조(비용) 부담한다."
        ok = {"verdict": "맞음", "action": "유지"}
        review = [{"claim": "반환 청구 (채용절차법 제11조)", "urls": [law + "/제11조"], **ok}]
        got = sc.delegations(review, fetcher=lambda u: text)
        self.assertEqual([(d["law"], d["article"]) for d in got], [("채용절차의공정화에관한법률", "제11조")])
        self.assertIn("대통령령으로 정한 반환의 청구기간", got[0]["sentences"][0])
        read = review + [{"claim": "14~180일", "urls": ["https://www.law.go.kr/법령/채용절차의공정화에관한법률시행령/제4조"], **ok}]
        self.assertEqual(sc.delegations(read, fetcher=lambda u: text), [])                 # 시행령도 읽었으면 조용
        self.assertEqual(sc.delegations([{"claim": "제12조", "urls": [law + "/제12조"], **ok}], fetcher=lambda u: text), [])
        self.assertIn("위임 확인", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))

    def test_addendum_transition_shown(self):
        """11/6 #973(시행규칙 부칙 제2조: 2019.10.18 전 면허는 2020~2022년)·#928(건축법 부칙 제5조) — 부칙 경과조치가 2번 빠짐 → 경고."""
        from bot import source_check as sc
        addenda = ("제1조(시행일) 이 규칙은 공포한 날부터 시행한다. 제2조(경과조치) 이 규칙 시행 전에 면허증을 발급 받은 자는 "
                   "제83조제3항의 개정규정에도 불구하고 다음 각 호의 구분에 따라 안전교육등을 받아야 한다. 제3조(다른 경과조치) 제84조는 종전에 따른다.")
        review = [{"claim": "3년마다 (시행규칙 제83조)", "urls": ["https://www.law.go.kr/법령/건설기계관리법시행규칙/제83조"],
                   "verdict": "맞음", "action": "유지"}]
        got = sc.addendum_transitions(review, addenda_of=lambda name: addenda)
        self.assertEqual([(g["law"], g["article"]) for g in got], [("건설기계관리법시행규칙", "제83조")])
        self.assertIn("제83조제3항의 개정규정에도 불구하고", got[0]["sentences"][0])
        other = [{"claim": "제84조", "urls": ["https://www.law.go.kr/법령/건설기계관리법시행규칙/제84조"], "verdict": "맞음", "action": "유지"}]
        self.assertEqual(sc.addendum_transitions(other, addenda_of=lambda name: addenda), [])   # '개정규정에도 불구하고'가 아님

    def test_unit_numbers_compared(self):
        """두 자리 수 + 단위(14일·24세·60시간·15회)도 대조 (11/6 'API 다 써서' 3번, #872 '14일 연장'). 날짜·한 자리·단위 없는 수는 뺌."""
        from bot.source_check import key_numbers
        self.assertEqual(key_numbers("조사가 더 필요하면 14일 연장"), ["14"])
        self.assertEqual(key_numbers("만 9~24세, 월 15회 이상"), ["15", "24"])
        for claim in ("계좌 개설 11월 16일(월)~27일(금)", "가입 심사 10월 19일~11월 13일", "2026년 10월 31일 시행", "5일 이내", "12억 이하"):
            self.assertEqual(key_numbers(claim), [], claim)

    def test_source_check_compares_with_official_text(self):
        from bot import benefit_detail, source_check
        with mock.patch.object(benefit_detail, "detail_text", return_value="문의처: 02-2100-6313 지원금액 월 65만원"), \
             mock.patch.object(source_check, "_fetch", return_value=None):      # 화면은 못 읽어도
            text = source_check.fetch("https://www.gov.kr/portal/rcvfvrSvc/dtlEx/WII000001600")
        self.assertIn("02-2100-6313", text)
        review = [{"claim": "문의는 02-2100-6313", "urls": ["https://www.gov.kr/portal/rcvfvrSvc/dtlEx/WII000001600"], "verdict": "맞음", "action": "유지"},
                  {"claim": "문의는 031-8008-2591", "urls": ["https://www.gov.kr/portal/rcvfvrSvc/dtlEx/WII000001600"], "verdict": "맞음", "action": "유지"}]
        result = source_check.check(review, fetcher=lambda u: text)
        self.assertEqual(result["counts"]["확인"], 1)
        self.assertEqual(result["counts"]["페이지에 없음"], 1)              # #872 경기도 번호

    def test_reviewer_gets_official_text(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import unreviewed
        post = {"content_html": '<a href="https://www.gov.kr/portal/rcvfvrSvc/dtlEx/WII000001600">정부24</a>',
                "source_check": {"found": [{"urls": ["https://www.gov.kr/portal/rcvfvrSvc/dtlEx/627000000759"]}]}}
        got = unreviewed.benefit_details(post, detail_text=lambda sid: f"원문 {sid}" if sid != "627000000759" else None)
        self.assertEqual(got, {"WII000001600": "원문 WII000001600"})
        self.assertIn("benefit_detail", (ROOT / "automation" / "review-prompt.md").read_text(encoding="utf-8"))
        self.assertIn("python3 -m bot.benefit_detail", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))


class LawRulesTest(unittest.TestCase):
    """행정규칙(고시)·자치법규(조례) 원문 (11/6 운영자 'API 다 써서' 2번): law.go.kr '/행정규칙/이름' 화면은 껍데기라
    근거 대조가 '못 엶'으로 끝나고 법령 감시도 안 봤음 (근거 주소 행정규칙 62곳·자치법규 14곳)."""

    SEARCH = ("<OrdinSearch><law id='1'><자치법규일련번호>100</자치법규일련번호><자치법규명><![CDATA[대구광역시 수도급수 조례 시행규칙]]></자치법규명>"
              "<시행일자>20250101</시행일자><공포일자>20241201</공포일자></law>"
              "<law id='2'><자치법규일련번호>200</자치법규일련번호><자치법규명><![CDATA[대구광역시 수도급수 조례 시행규칙]]></자치법규명>"
              "<시행일자>20260901</시행일자><공포일자>20260625</공포일자></law>"
              "<law id='3'><자치법규일련번호>300</자치법규일련번호><자치법규명><![CDATA[대구광역시 수도급수 조례]]></자치법규명>"
              "<시행일자>20260101</시행일자></law></OrdinSearch>")
    BODY = ("<LawService><조><조내용><![CDATA[제18조의4(다자녀가정 감면) 1. 자녀가 3명 이상: 월 3,000원]]></조내용></조>"
            "<별표><별표내용><![CDATA[<table><tr><td>가정용 1,000원</td></tr></table>]]></별표내용></별표></LawService>")
    ADM = ("<AdmRulService><조문내용><![CDATA[]]></조문내용><첨부파일><첨부파일링크>http://law.go.kr/flDownload.do?flSeq=1 </첨부파일링크>"
           "</첨부파일></AdmRulService>")

    def fetch(self, kind, target, **kw):
        if kind == "search":
            return self.SEARCH
        return self.BODY if kw.get("MST") == "200" else (self.ADM if target == "admrul" else "")

    def test_rule_of_url(self):
        from bot import law_rules as lr
        self.assertEqual(lr.rule_of("https://www.law.go.kr/%EC%9E%90%EC%B9%98%EB%B2%95%EA%B7%9C/대구광역시수도급수조례/제39조"),
                         ("자치법규", "대구광역시수도급수조례"))
        self.assertEqual(lr.rule_of("https://www.law.go.kr/행정규칙/2026년적용최저임금고시"), ("행정규칙", "2026년적용최저임금고시"))
        self.assertIsNone(lr.rule_of("https://www.law.go.kr/법령/고용보험법"))

    def test_current_version_text(self):
        import datetime as dt
        from bot import law_rules as lr
        vers = lr.versions("자치법규", "대구광역시수도급수조례시행규칙", fetch=self.fetch)   # 띄어쓰기 무관, 이름이 다른 조례는 뺌
        self.assertEqual([v["seq"] for v in vers], ["100", "200"])
        self.assertEqual(lr.current(vers, dt.date(2026, 11, 6))["seq"], "200")
        self.assertEqual(lr.current(vers, dt.date(2026, 8, 1))["seq"], "100")                  # 시행 전 판은 아님
        text = lr.rule_text("자치법규", "대구광역시수도급수조례시행규칙", dt.date(2026, 11, 6), fetch=self.fetch)
        self.assertIn("월 3000원", text)                                                       # 쉼표 뺌 (숫자 대조용)
        self.assertIn("가정용 1000원", text)                                                   # 별표 표도

    def test_notice_text_from_attachment(self):
        from bot import law_rules as lr, source_check
        with mock.patch.object(source_check, "doc_text", return_value="시간급 10320원"):
            text = lr.body_text("행정규칙", "1", fetch=self.fetch, raw_of=lambda url: (b"%PDF", None, "") if url.startswith("https://") else None)
        self.assertIn("10320원", text)                                                         # 고시 본문은 첨부 PDF 에만

    def test_source_check_and_lawtext_use_rules(self):
        from bot import law_rules, source_check
        with mock.patch.object(law_rules, "rule_text", return_value="월 3000원") as rt:
            self.assertEqual(source_check._fetch("https://www.law.go.kr/자치법규/대구광역시수도급수조례시행규칙"), "월 3000원")
        rt.assert_called_once_with("자치법규", "대구광역시수도급수조례시행규칙")
        self.assertIn("_rule_body", (ROOT / "bot" / "lawtext.py").read_text(encoding="utf-8"))

    def test_watch_marks_posts_when_rule_amended(self):
        import datetime as dt
        from bot import law_watch
        rules = {("자치법규", "대구광역시수도급수조례시행규칙"): {"대구다자녀상수도요금감면"}}
        vers = [{"seq": "100", "start": dt.date(2025, 1, 1)}, {"seq": "200", "start": dt.date(2026, 9, 1)},
                {"seq": "300", "start": dt.date(2027, 1, 1)}]
        up, just, seen = law_watch.rule_changes(rules, {}, dt.date(2026, 11, 6), versions=lambda k, n: vers)
        self.assertEqual(just, [])                                                             # 처음 보는 것은 기록만
        self.assertEqual(seen, {"자치법규|대구광역시수도급수조례시행규칙": "200"})
        self.assertEqual([u["date"] for u in up], ["2027-01-01"])                               # 시행 예정 판은 글감
        up, just, seen = law_watch.rule_changes(rules, {"자치법규|대구광역시수도급수조례시행규칙": "100"}, dt.date(2026, 11, 6),
                                                versions=lambda k, n: vers)
        self.assertEqual([(j["posts"], j["date"]) for j in just], [(["대구다자녀상수도요금감면"], "2026-09-01")])  # 바뀐 판 → 재검증


class CalcHomeTest(unittest.TestCase):
    """계산기 대표 글 (11/6 운영자): 계산기를 넣은 공개 글이 있으면 단독 페이지는 그 글로 301·목록 링크도 그 글·사이트맵에서 뺌.
    공개 글이 없으면 단독 페이지가 그대로 (계산기가 사라지지 않게)."""

    STUB = r"""<?php
define('ABSPATH', '/'); define('ARRAY_A', 'ARRAY_A');
class WP_Post { function __construct(public $ID, public $post_type, public $post_name) {} }
$GLOBALS['hooks'] = array();
function add_action($h, $f) { $GLOBALS['hooks'][$h] = $f; }
function add_filter($h, $f) { $GLOBALS['hooks'][$h] = $f; }
class FakeDb { public $posts = 'wp_posts';
  function esc_like($t) { return $t; }
  function prepare($q, $like) { return $like; }
  function get_results($like, $mode) {   // 공개 글 (먼저 공개된 순)
    $all = array(array('id' => 10, 'title' => '양도세 계산 방법', 'body' => '[firstlab_calc id="capital-gains-tax"]'),
                 array('id' => 11, 'title' => '부동산 양도세 계산기', 'body' => '[firstlab_calc id="capital-gains-tax"]'),
                 array('id' => 12, 'title' => '대출 이자', 'body' => '[firstlab_calc id="loan-repayment"]'));
    $needle = trim($like, '%');
    return array_values(array_filter($all, fn($r) => str_contains($r['body'], $needle)));
  } }
$wpdb = new FakeDb();
function get_post($id) { return new WP_Post($id, 'post', "p$id"); }
function get_permalink($p) { return "/{$p->post_name}/"; }
function get_pages($a) { return array(new WP_Post(1, 'page', 'capital-gains-tax-calculator'), new WP_Post(2, 'page', 'vat-calculator'),
                                      new WP_Post(3, 'page', 'about')); }
require getenv('FL_PLUGIN');
$cg = new WP_Post(1, 'page', 'capital-gains-tax-calculator'); $vat = new WP_Post(2, 'page', 'vat-calculator');
echo json_encode(array(
  'id' => firstlab_calc_id_of_slug('capital-gains-tax-calculator'), 'not_calc' => firstlab_calc_id_of_slug('about'),
  'target' => firstlab_calc_page_target($cg)?->ID, 'none' => firstlab_calc_page_target($vat),
  'link_cg' => firstlab_calc_link($cg), 'link_vat' => firstlab_calc_link($vat),
  'post_not_page' => firstlab_calc_page_target(new WP_Post(11, 'post', 'capital-gains-tax-calculator')),
  'sitemap_skip' => $GLOBALS['hooks']['the_seo_framework_sitemap_exclude_ids'](array(99)),
  'pick_first' => firstlab_calc_pick_home(array(array('id' => 5, 'title' => '방법'), array('id' => 6, 'title' => '설명'))),
  'pick_none' => firstlab_calc_pick_home(array()),
));
"""

    def run_php(self):
        import os
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("php"):
            self.skipTest("php 없음")
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(self.STUB)
        try:
            env = {**os.environ, "FL_PLUGIN": str(ROOT / "wordpress" / "mu-plugins" / "first-lab-calc-home.php")}
            return json.loads(subprocess.run(["php", f.name], capture_output=True, text=True, env=env, check=True).stdout)
        finally:
            os.unlink(f.name)

    def test_calc_page_goes_to_published_post(self):
        r = self.run_php()
        self.assertEqual((r["id"], r["not_calc"]), ("capital-gains-tax", ""))
        self.assertEqual(r["target"], 11)                         # 제목에 '계산기'가 든 글 먼저 (먼저 공개된 10 보다)
        self.assertIsNone(r["none"])                               # 넣은 공개 글이 없으면 단독 페이지 그대로
        self.assertEqual((r["link_cg"], r["link_vat"]), ("/p11/", "/vat-calculator/"))
        self.assertIsNone(r["post_not_page"])                      # 글 자체는 넘기지 않음
        self.assertEqual(r["sitemap_skip"], [99, 1])               # 넘어가는 페이지만 사이트맵에서 뺌
        self.assertEqual((r["pick_first"], r["pick_none"]), (5, None))

    def test_list_and_related_use_home_post(self):
        home = (ROOT / "wordpress" / "mu-plugins" / "first-lab-home.php").read_text(encoding="utf-8")
        self.assertIn("firstlab_calc_link( $p )", home)
        self.assertIn("firstlab_calc_page_target", (ROOT / "bot" / "related.py").read_text(encoding="utf-8"))


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


class SilentFailureTest(unittest.TestCase):
    """성공 기록 속 일부 실패도 ❌ (11/6 #1023 '근거 대조 실패(기록만)' 이 성공으로 남아 매일 점검이 못 봄) + 품질 한 줄 보고."""

    def test_partial_failure_marked(self):
        from bot.state import add_log
        state = {"log": []}
        add_log(state, True, "직접 작성 글: [공개 대기] 'x' (근거 대조 실패(기록만): could not convert)")
        add_log(state, True, "서버 상태: 사주데이 응답 최대 1076ms·실패 0/41")
        add_log(state, True, "예약 공개 19:20: 'x' 공개 (IndexNow 알림 실패 429)")
        add_log(state, True, "글 401 근거 대조 다시: 숫자 주장 15개 중 확인 10")
        self.assertEqual([e["ok"] for e in state["log"]], [True, False, True, False])

    def test_quality_line(self):
        import datetime as dt
        sys.path.insert(0, str(ROOT / "automation"))
        import quality_report as q
        today = dt.date(2026, 11, 13)
        rows = [{"date": "2026-11-06", "fixes": "9"}, {"date": "2026-11-07", "fixes": "1"}, {"date": "2026-11-12", "fixes": "0"}]
        n, avg, line = q.summary(rows, today)
        self.assertEqual((n, avg), (2, 0.5))   # 7일 전(11/6)은 뺌
        self.assertNotIn("⚠", line)
        self.assertIn("⚠", q.summary(rows + [{"date": "2026-11-13", "fixes": "5"}], today)[2])
        self.assertIn("기록 없음", q.summary([], today)[2])   # 검수가 멈춘 것도 알림

    def test_wired(self):
        self.assertIn("quality_report.py", (ROOT / "automation" / "maintenance.md").read_text(encoding="utf-8"))
        self.assertIn("quality_report", (ROOT / "automation" / "tick.py").read_text(encoding="utf-8"))


class AppendOnlyMergeTest(unittest.TestCase):
    """끝에 줄만 붙이는 기록 파일은 동시에 붙여도 충돌 없이 양쪽을 남김 (11/6 reviewed.txt 충돌로 감시 세션 push 실패)."""

    def test_union_merge(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            run = lambda *a: subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True)  # noqa: E731
            run("init", "-q", "-b", "main")
            run("config", "user.email", "t@t"); run("config", "user.name", "t")
            (Path(d) / ".gitattributes").write_text((ROOT / ".gitattributes").read_text(encoding="utf-8"), encoding="utf-8")
            (Path(d) / "automation").mkdir()
            f = Path(d) / "automation" / "reviewed.txt"
            f.write_text("1 a\n", encoding="utf-8"); run("add", "-A"); run("commit", "-qm", "base")
            run("checkout", "-qb", "other"); f.write_text("1 a\ncalc:x\n", encoding="utf-8"); run("commit", "-qam", "o")
            run("checkout", "-q", "main"); f.write_text("1 a\n2 b\n", encoding="utf-8"); run("commit", "-qam", "m")
            run("merge", "-q", "--no-edit", "other")
            self.assertEqual(sorted(f.read_text(encoding="utf-8").split("\n")[:3]), ["1 a", "2 b", "calc:x"])


    def test_generated_checklist_regenerated_not_unioned(self):
        """11/7: 자주 틀린 유형표(생성 파일)를 union 으로 병합해 줄이 섞이고 테스트가 깨짐 → ours + 올리기 전 다시 만듦."""
        attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("automation/common-errors.md merge=ours", attrs)
        self.assertNotIn("common-errors.md merge=union", attrs)
        push = (ROOT / "automation" / "check_and_push.sh").read_text(encoding="utf-8")
        self.assertIn("merge.ours.driver", push)
        self.assertIn("python3 automation/common_errors.py", push)
        self.assertIn("merge.ours.driver", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))

class RouteArrowTest(unittest.TestCase):
    """#1060 대장경테마파크 (11/6): 길 화살표 주장('대구→고령→해인사행 하루 21회')을 계산으로 봐서 숫자 대조를 안 했고,
    검수 반영 도구가 검수 기록을 안 붙여 review 를 보는 검사가 늘 '안 걸림', 시각 10:30 을 확인된 숫자 10 으로 셈."""

    def test_route_arrow_is_not_calc(self):
        from bot.source_check import is_calc_claim, key_numbers
        self.assertEqual(key_numbers("대구 서부시외버스터미널→고령→해인사행 하루 21회(40분 간격)"), ["21"])
        self.assertTrue(is_calc_claim("월 300만 원 → 270만 원"))
        self.assertEqual(key_numbers("1,000원→900원"), [])

    def test_time_is_not_the_number(self):
        from bot.source_check import has_number
        self.assertFalse(has_number("하루 3회 (06:40, 10:30, 15:30)", "10"))
        self.assertTrue(has_number("외국인 10명 이상", "10"))

    def test_apply_review_attaches_review(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        with tempfile.TemporaryDirectory() as d:
            q = Path(d)
            (q / "x.json").write_text(json.dumps({"title": "글 제목", "review": [{"claim": "c"}]}, ensure_ascii=False), encoding="utf-8")
            got = apply_review.with_review({"post_id": 7, "title": "글 제목"}, q)
            self.assertEqual(got["review"], [{"claim": "c"}])
            (q / "fixhold-7-1.json").write_text(json.dumps({"review": [{"claim": "고친 뒤"}]}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(apply_review.with_review({"post_id": 7, "title": "글 제목"}, q)["review"], [{"claim": "고친 뒤"}])

    def test_other_posts_fixhold_review_not_attached(self):
        """11/6 #1178: 다른 글(fixhold-1122-…)의 검수 기록이 붙어 단서 검사가 엉뚱한 법령을 봤음."""
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        with tempfile.TemporaryDirectory() as d:
            q = Path(d)
            (q / "fixhold-3-1.json").write_text(json.dumps({"review": [{"claim": "남의 글"}]}, ensure_ascii=False), encoding="utf-8")
            (q / "y.json").write_text(json.dumps({"title": "글 제목", "review": [{"claim": "내 글"}]}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(apply_review.with_review({"post_id": 7, "title": "글 제목"}, q)["review"], [{"claim": "내 글"}])


class ProvisoChecklistTest(unittest.TestCase):
    """근거 조문의 단서·예외 확인표 (11/6 운영자 '품질 1개 미만 — 1,2번 만들어라'): 외부 검수가 고친 곳의 74%가 빠진 조건·예외."""

    ART = {"제41조의2": "제41조의2(소득월액의 조정 등) ① 가입자는 조정을 신청할 수 있다. "
                        "⑧ 제1항에도 불구하고 간이지급명세서가 제출된 경우에는 증명서류를 첨부한 것으로 본다. "
                        "다만, 공단이 누락ㆍ오류 등으로 확인할 수 없는 경우에는 그러하지 아니하다."}
    URL = "https://www.law.go.kr/법령/국민건강보험법시행령/제41조의2"

    def review(self, *extra):
        return [{"claim": "간이지급명세서가 제출되면 증빙 첨부로 봐요 (국민건강보험법시행령 제41조의2)",
                 "verdict": "맞음", "action": "유지", "urls": [self.URL]}, *extra]

    def test_each_proviso_needs_a_record(self):
        from bot.condition_gate import article_provisos
        got = article_provisos(self.review(), articles_of=lambda base: self.ART)
        self.assertEqual(len(got), 2)                       # ⑧ '에도 불구하고', 다만 단서
        self.assertIn("단서2", got[1])
        done = [{"claim": "제41조의2 단서1 반영 — 둘째 문단", "verdict": "맞음", "action": "유지", "urls": [self.URL]},
                {"claim": "제41조의2 단서2 반영 — 요약과 둘째 문단 '다만 누락·오류'", "verdict": "맞음", "action": "유지", "urls": [self.URL]}]
        self.assertEqual(article_provisos(self.review(*done), articles_of=lambda base: self.ART), [])
        half = [done[0], {"claim": "제41조의2 단서2", "verdict": "맞음", "action": "유지", "urls": [self.URL]}]
        self.assertEqual(len(article_provisos(self.review(*half), articles_of=lambda base: self.ART)), 1)  # 반영/해당 없음 말이 있어야

    def test_not_applied_clause_is_a_proviso(self):
        """'…이면 제4호의 가산세를 적용하지 아니한다' 도 단서 (11/6 #1093 지방세기본법 제55조④ 45만 원 기준을 고지서 합계로 씀)."""
        from bot.source_check import exceptions_in
        art = "제55조(납부지연가산세) ① 가산세를 부과한다. ④ 제1항을 적용할 때 납세고지서별ㆍ세목별 세액이 45만원 미만인 경우에는 같은 항 제4호의 가산세를 적용하지 아니한다."
        self.assertTrue(any("45만원 미만" in x for x in exceptions_in(art)))

    def test_long_list_shows_the_item_with_exception(self):
        """호를 나열한 긴 항은 예외가 든 호만 (11/6 #1143 제26조②7호 괄호 '…경우는 제외한다' 가 앞 200자에 잘려 안 보였음)."""
        from bot.source_check import exceptions_in
        items = " ".join(f"{i}. 제{i}조를 위반하여 신고를 하지 아니한 자" for i in range(1, 7))
        art = f"제26조(과태료) ② 다음 각 호의 어느 하나에 해당하는 자에게는 300만원 이하의 과태료를 부과한다. {items} 7. 공제부금을 내지 아니한 자(도급인이 직접 내야 하는 경우는 제외한다)"
        got = exceptions_in(art)
        self.assertTrue(any(x.startswith("7.") or "7. 공제부금" in x for x in got), got)

    def test_unreadable_law_does_not_block(self):
        from bot.condition_gate import article_provisos
        self.assertEqual(article_provisos(self.review(), articles_of=lambda base: {}), [])

    def test_wired(self):
        self.assertIn("article_provisos", (ROOT / "bot" / "review_gate.py").read_text(encoding="utf-8"))
        writer = (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m bot.provisos", writer)
        self.assertIn("`automation/review-prompt.md` 의 `---` 아래 지시문 그대로", writer)   # 2번: 독립 검수 = 외부 검수 지시


class ExcerptParentheticalTest(unittest.TestCase):
    """요약이 본문 괄호 속 제외 조건을 뺌 (11/6 #1134 '사망했을 때(유족연금이 나오면 제외)' ↔ 요약 '사망')."""

    def test_parenthetical_condition(self):
        from bot.condition_gate import excerpt_parentheticals
        body = "<p>가입자가 사망했을 때(유족연금이 나오면 제외), 국외로 이주했을 때 받아요.</p>"
        self.assertEqual(len(excerpt_parentheticals("10년 미만, 사망, 국외 이주 때만 받아요.", body)), 1)
        self.assertEqual(excerpt_parentheticals("사망(유족연금이 나오지 않을 때), 국외 이주 때만 받아요.", body), [])
        self.assertEqual(excerpt_parentheticals("국외 이주 때 받아요.", body), [])
        self.assertIn('"excerpt_parentheticals"', (ROOT / "automation" / "apply_review.py").read_text(encoding="utf-8"))


class DeadlineHolidayTest(unittest.TestCase):
    """예시 기한이 토·일·공휴일인데 다음 날로 늦춰짐을 안 씀 (11/6 #1126 증여세 '2월 말일까지' — 2027-02-28 일요일·3/1 삼일절)."""

    def test_example_deadline_on_weekend(self):
        import datetime as dt
        from bot.condition_gate import deadline_holiday
        today = dt.date(2026, 11, 6)
        bad = "<p>예를 들어 11월 6일에 받았다면 11월 30일부터 3개월, 즉 2월 말일까지예요.</p>"
        self.assertIn("2027-02-28(일요일)", deadline_holiday(bad, today)[0])
        ok = "<p>예를 들어 2월 28일이 일요일이고 3월 1일이 공휴일이라 기한은 3월 2일까지로 늦춰져요. 예를 들어 2월 26일까지 내요.</p>"
        self.assertEqual(deadline_holiday(ok, today), [])          # 늦춰짐을 썼거나 평일(2/26 금)
        self.assertEqual(deadline_holiday("<p>12월 31일까지 신청해요.</p>", today), [])   # 예시 문장이 아니면 안 봄


class FacilityOrdinanceTest(unittest.TestCase):
    """공공시설 글은 그 시설의 설치·운영 조례를 근거로 (11/6 #1106 인천시립요양원: 보도자료만 보고 입소 대상을 좁게 씀)."""

    def test_needs_own_ordinance(self):
        from bot.condition_gate import facility_ordinance
        other = [{"claim": "c", "urls": ["https://www.law.go.kr/자치법규/인천광역시계양구치매전담형노인요양시설설치및운영조례"]}]
        own = [{"claim": "c", "urls": ["https://www.law.go.kr/자치법규/인천광역시립요양원설치및운영에관한조례/제5조"]}]
        self.assertEqual(len(facility_ordinance("인천시립요양원 입소 조건과 비용", other)), 1)
        self.assertEqual(facility_ordinance("인천시립요양원 입소 조건과 비용", own), [])
        self.assertEqual(facility_ordinance("천안 청수도서관 이용시간", []), [])          # 시립·구립이 제목에 없으면 안 봄
        # 11/7 #1297 함허동천: 이름에 군립이 없어도 본문이 지자체·공단 운영이면 조례 확인 (군청 안내 페이지가 옛 요금)
        self.assertEqual(len(facility_ordinance("함허동천 캠핑장 요금", [], "", "<p>강화군시설관리공단이 운영하는 야영장이에요.</p>")), 1)
        self.assertEqual(facility_ordinance("함허동천 캠핑장 요금", [{"claim": "요금", "urls": ["https://www.law.go.kr/자치법규/강화군함허동천야영장관리및운영조례"]}],
                                            "", "<p>강화군시설관리공단이 운영해요.</p>"), [])
        self.assertEqual(facility_ordinance("구립 도서관 이용시간", [{"claim": "조례 해당 없음 — 구 규칙만 있음", "urls": []}]), [])
        # 제목엔 없고 요약에만 '시립' (#1156), 여러 시설 공통 조례는 '시설 조례:' 로 밝히면 통과
        self.assertEqual(len(facility_ordinance("남부대 수영장 이용 안내", [], "광주 남부대학교 시립국제수영장 성인 일일 5,000원")), 1)
        umbrella = [{"claim": "시설 조례: 광주광역시체육시설관리운영조례 별표 1 27호 남부대시립국제수영장",
                     "urls": ["https://www.law.go.kr/자치법규/광주광역시체육시설관리운영조례/제17조"]}]
        self.assertEqual(facility_ordinance("남부대 수영장 이용 안내", umbrella, "남부대학교 시립국제수영장"), [])


class LawAnnexTest(unittest.TestCase):
    """11/6 #1189·#1199: 과태료·봉급표 숫자가 법령 별표에만 있어 근거 대조가 '페이지에 없음' — 법령 근거면 현행 판 별표 글자도 대조."""

    def test_law_page_includes_annex(self):
        from unittest import mock
        from bot import source_check
        url = "https://www.law.go.kr/법령/공무원보수규정-테스트"
        with mock.patch.object(source_check, "_fetch", return_value="제5조 봉급"), \
                mock.patch.object(source_check, "law_annex", return_value="9급 1호봉 2133000"), \
                mock.patch("bot.condition_gate._cached", side_effect=lambda kind, key, make: make()):
            self.assertIn("2133000", source_check.fetch(url))

    def test_non_law_url_has_no_annex(self):
        from bot import source_check
        self.assertEqual(source_check.law_annex("https://www.moel.go.kr/x"), "")

    def test_annex_text_reads_byl(self):
        from unittest import mock
        from bot import law_api
        xml = "<법령><별표단위><별표내용><![CDATA[■ 공무원보수규정 [별표 13] <개정 2026. 1. 2.> 9급 2,133,000]]></별표내용></별표단위></법령>"
        import datetime as dt
        with mock.patch.object(law_api, "fetch", return_value=xml):
            self.assertIn("2,133,000", law_api.annex_text({"mst": "1", "date": dt.date(2026, 1, 2)}))


class ExpiryTest(unittest.TestCase):
    """11/7 운영자 '근본적인 해결책 — 글들은 대부분 나중에 업데이트가 필요할 거 아냐': 글마다 다시 확인할 날 (bot/expiry.py)."""

    def setUp(self):
        import datetime as dt
        self.dt = dt
        self.day = dt.date(2026, 11, 7)

    def test_base_and_unreadable_cycle(self):
        from bot.expiry import due_date
        self.assertEqual(due_date("숫자 없는 안내", self.day)[0], self.day + self.dt.timedelta(days=180))
        self.assertEqual(due_date("숫자 없는 안내", self.day, unreadable=True)[0], self.day + self.dt.timedelta(days=90))

    def test_new_year_for_this_year_values(self):
        from bot.expiry import due_date
        self.assertEqual(due_date("2026년 최저임금은 시급 10,320원이에요.", self.day)[0], self.dt.date(2027, 1, 5))

    def test_announced_change_date(self):
        """#1226 폐렴: '2027년도 예산안(2026년 9월 발표)에서 … 바꿀 예정' → 2027-01-01 (지난 발표 날짜로 매달 돌지 않음)."""
        from bot.expiry import due_date
        text = "질병관리청은 2027년도 예산안(2026년 9월 발표)에서 어르신 백신을 단백결합 백신으로 바꿀 예정이라고 밝혔어요."
        self.assertEqual(due_date(text, self.day)[0], self.dt.date(2027, 1, 1))
        self.assertEqual(due_date("2026년 12월 10일부터 휴게시간 규정이 바뀌어요.", self.day)[0], self.dt.date(2026, 12, 10))

    def test_stale_plan_rechecked_monthly(self):
        """예정 날짜가 지났는데 아직 '예정'이면 30일마다 (바뀐 뒤 고칠 때까지)."""
        from bot.expiry import due_date
        got = due_date("2027년부터 단백결합 백신으로 바꿀 예정이에요.", self.dt.date(2027, 1, 3))
        self.assertEqual(got[0], self.dt.date(2027, 2, 2))

    def test_unreadable_from_record(self):
        from bot.expiry import unreadable
        self.assertTrue(unreadable({"counts": {"확인": 5, "못 엶": 8}}))
        self.assertFalse(unreadable({"counts": {"확인": 9, "못 엶": 1}}))
        self.assertFalse(unreadable(None))

    def test_yearless_date_counts(self):
        """#1423 '11월 19일 접수'처럼 연도 없는 날짜도 다시 확인할 날이 됨 (예전엔 'O년 O월'만 읽어 놓침)."""
        from bot.expiry import due_date
        self.assertEqual(due_date("12월 시험은 11월 19일 오후 2시부터 선착순 접수예요.", self.day)[0], self.dt.date(2026, 11, 19))
        # 11월에 쓴 '1월 9일' 은 다음 해
        self.assertEqual(due_date("다음 접수는 1월 9일부터예요.", self.day)[0], self.dt.date(2027, 1, 9))

    def test_draft_round_passed_before_publish(self):
        """11/7 운영자 '시간 제한 있는 글은 나중에 올리면 기한이 지나지 않아?': 쓸 땐 앞날이던 접수가 대기 중 지나면 보류."""
        from bot.expiry import passed_rounds
        written = self.dt.date(2026, 11, 6)
        texts = ["12월 시험은 11월 19일(목) 오후 2시 선착순 접수"]
        self.assertEqual(passed_rounds(texts, written, self.dt.date(2026, 11, 18)), [])
        self.assertIn("2026-11-19", passed_rounds(texts, written, self.dt.date(2026, 11, 20))[0])
        # 기간은 끝날까지 유효, 이미 지난 일로 쓴 문장·해마다 같은 날·규칙 변경('이후')은 아님
        self.assertEqual(passed_rounds(["11월 2일~30일 접수"], written, self.dt.date(2026, 11, 20)), [])
        self.assertTrue(passed_rounds(["11월 2일~30일 접수"], self.dt.date(2026, 11, 1), self.dt.date(2026, 12, 1)))
        for text in ("2차 신청은 11월 10일 마감됐어요", "매년 11월 30일까지 신청해요", "11월 12일 이후 건축허가 신청분부터 적용"):
            self.assertEqual(passed_rounds([text], written, self.dt.date(2026, 12, 1)), [], text)

    def test_round_headline_for_repeating_schedule(self):
        """11/7 운영자 '시험일정이 아니라 계속 유효한 걸로 적어야': 되풀이 일정인데 제목·요약이 이번 회차에 묶이면 관문에서 막음."""
        from bot import review_gate
        from bot.expiry import blocks, round_headline
        body = "<p>소방안전관리자 2급 시험은 매달 열려요.</p><table><tr><td>월별 공고</td></tr></table>"
        got = round_headline("소방안전관리자 2급 시험일정: 12월 시험은 11월 19일 접수", "", " ".join(blocks(body)))
        self.assertEqual(len(got), 1)
        self.assertIn("늘 맞는 규칙", got[0])
        self.assertEqual(round_headline("소방안전관리자 2급 시험일정: 매달 시행, 전달 중순 오후 2시 접수", "", " ".join(blocks(body))), [])
        self.assertEqual(round_headline("아동수당: 2027년 1월 21일 이후 출생아는 신청 없이", "", "매월 10만 원"), [])   # 규칙 변경
        self.assertEqual(round_headline("한정 행사 11월 30일까지 접수", "", "올해만 열리는 행사"), [])          # 한 번뿐인 행사는 공개 전 확인이 막음
        self.assertTrue(any("이번 회차" in p for p in review_gate.problems([{"claim": "c"}], "12월 시험은 11월 19일 접수", "", body)))

    def test_queued_draft_with_passed_round_held(self):
        from bot import freshness

        def wp(*a):
            if a[:2] == ("post", "get"):
                return {"--field=post_title": "소방안전관리자 2급 시험일정", "--field=post_excerpt": "",
                        "--field=post_content": "<p>12월 시험은 11월 19일 오후 2시 선착순 접수예요.</p>",
                        "--field=post_date": "2026-11-06", "--field=post_name": "x"}.get(a[3], "")
            return ""
        with mock.patch.object(freshness, "wp_cli", side_effect=wp), mock.patch.object(freshness, "problems", side_effect=lambda *a, **k: []), \
                mock.patch.object(freshness, "_review_from_queue", return_value=[{"claim": "c"}]):
            self.assertFalse(any("지난 일정" in g for g in freshness.check_post(1, self.dt.date(2026, 11, 18))))
            self.assertTrue(any("지난 일정" in g for g in freshness.check_post(1, self.dt.date(2026, 11, 20))))

    def test_due_post_picked_regardless_of_weekly_limit(self):
        from unittest import mock
        from bot import refresh
        post = {"id": 1, "title": "폐렴 예방접종 가격", "url": "u", "date": "2026-05-01", "refreshed": "", "verified": "",
                "law_changed": "", "has_number": True, "text": "65세 이상 무료", "source_check": ""}
        conn = mock.MagicMock()
        real = refresh.datetime
        with mock.patch.object(refresh, "wp_cli", return_value=json.dumps([post])), \
                mock.patch.object(refresh, "datetime") as fake:
            fake.now.return_value = real(2026, 11, 7)
            fake.strptime = real.strptime
            got = refresh.pick_targets(conn, 1, fixes_only=True)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1], "recheck")
        self.assertIn("확인 기한", got[0][2])

    def test_chain_stops_at_daily_cap(self):
        from unittest import mock
        from bot import refresh
        state = {}
        with mock.patch.object(refresh, "plan_direct") as plan:
            for _ in range(refresh.RECHECK_PER_DAY + 2):
                refresh._chain_next(state)
        self.assertEqual(plan.call_count, refresh.RECHECK_PER_DAY - 1)


class MiddleDotRuleNameTest(unittest.TestCase):
    """11/7 #998: 이름에 '·' 가 있는 조례('동해시 망상오토캠핑리조트 관리· 운영 조례')를 법제처 검색이 0건으로 돌려줘 못 읽음."""

    def test_query_without_dot_and_name_matches(self):
        from bot import law_rules
        asked = []
        xml = ("<LawSearch><law><자치법규명>동해시 망상오토캠핑리조트 관리· 운영 조례</자치법규명><자치법규일련번호>2129089</자치법규일련번호>"
               "<시행일자>20260512</시행일자><공포일자>20260512</공포일자></law></LawSearch>")

        def fake(kind, target, **kw):
            asked.append(kw.get("query"))
            return xml
        got = law_rules.versions("자치법규", "동해시망상오토캠핑리조트관리·운영조례", fetch=fake)
        self.assertNotIn("·", asked[0])
        self.assertEqual([v["seq"] for v in got], ["2129089"])


class OrdinanceAnnexTest(unittest.TestCase):
    """11/7 #1288: 자치법규 별표는 본문이 비고 '<별표첨부파일명>' 의 hwp 로만 있어 조례 요금표(S사이트 50,000원·1박 추가 30,000원)를 못 읽음."""

    def test_annex_attachment_read(self):
        from bot import law_rules
        xml = ("<별표단위><별표첨부파일구분><![CDATA[hwp]]></별표첨부파일구분><별표첨부파일명><![CDATA[http://www.law.go.kr/flDownload.do?"
               "gubun=ELIS&flSeq=137245517&flNm=%EB%B3%84%ED%91%9C+1]]></별표첨부파일명><별표내용><![CDATA[]]></별표내용></별표단위>")
        seen = []
        law_rules._docs_text(xml, raw_of=lambda url: seen.append(url) or None)
        self.assertEqual(seen, ["https://www.law.go.kr/flDownload.do?gubun=ELIS&flSeq=137245517&flNm=%EB%B3%84%ED%91%9C+1"])


class ImageAltTextTest(unittest.TestCase):
    """11/7 #1245: 공단 사진 규격 기준이 그림 설명 글(alt)에만 있어 근거 대조가 '못 엶'."""

    def test_alt_text_read(self):
        from bot.source_check import page_text
        raw = '<html><body><img src="a.png" alt="파일 크기 500킬로바이트(KB) 이하의 JPG 파일, 413 x 531 픽셀 권장"></body></html>'.encode()
        self.assertIn("500킬로바이트", page_text(raw, "utf-8"))
        self.assertNotIn("로고", page_text('<img alt="로고">'.encode(), "utf-8"))  # 짧은 alt 는 뺌


class MillionthRateTest(unittest.TestCase):
    """11/6 #1228: 노인장기요양보험법 시행령 '100만분의 9,448'(0.9448%)을 같은 값으로 못 봐 '페이지에 없음'."""

    def test_millionth_form(self):
        import re
        from bot.source_check import _fraction_forms
        page = "장기요양보험료율은 100만분의 9448로 한다"  # 근거 대조는 쉼표를 뺀 글자로 비교
        self.assertTrue(any(re.search(f, page) for f in _fraction_forms("0.9448")))


class Hwp5TextTest(unittest.TestCase):
    """11/6 #1210: 대지급금 상한액 고시 첨부가 HWP 5.0 이라 근거 대조가 빈 글자 → 맞는 '310만 원'이 '페이지에 없음'.
    파일: 법제처 고시 첨부 원본 (flSeq=108704833, 공공 문서)."""

    @unittest.skipUnless(importlib.util.find_spec("olefile"), "olefile 없음")
    def test_reads_hwp5_attachment(self):
        from bot.source_check import doc_text
        text = doc_text((ROOT / "tests" / "fixtures" / "daejigeupgeum-gosi.hwp").read_bytes())
        self.assertIn("출산전후휴가기간 중 급여", text)
        self.assertIn("310", text)

    def test_broken_hwp_is_empty(self):
        from bot.source_check import doc_text
        self.assertEqual(doc_text(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"broken"), "")

    def test_olefile_in_server_requirements(self):
        self.assertIn("olefile", (ROOT / "bot" / "requirements.txt").read_text(encoding="utf-8"))


class RuleProvisoTest(unittest.TestCase):
    """11/6 #1218: 조례 제7조② 단서('긴급상황 시 다르게 적용')를 뺌 — 단서 확인표가 법령(/법령/)만 보고 조례·고시는 빈 조문이었음."""
    TEXT = ("제6조(예약) ① 숲나들e로 예약한다. 다만 예약시스템을 쓸 수 없으면 전화로 예약할 수 있다. "
            "제7조(이용료의 반환 등) ② 5일 이내에 반환한다. 다만 긴급상황 발생 시 다르게 적용할 수 있다. "
            "부 칙 이 조례는 공포한 날부터 시행한다. [별표 1] 이용료")

    def test_rule_split_into_articles(self):
        from bot.condition_gate import rule_articles
        arts = rule_articles(self.TEXT)
        self.assertEqual(list(arts), ["제6조", "제7조"])
        self.assertIn("긴급상황", arts["제7조"])
        self.assertNotIn("별표", arts["제7조"])

    def test_ordinance_proviso_required(self):
        from unittest import mock
        from bot import condition_gate
        review = [{"claim": "환불은 5일 안에 (사천시 자연휴양림 관리 및 운영에 관한 조례 제7조)", "verdict": "맞음", "action": "유지",
                   "urls": ["https://www.law.go.kr/자치법규/사천시자연휴양림관리및운영에관한조례"]}]
        with mock.patch("bot.law_rules.rule_text", return_value=self.TEXT):
            got = condition_gate.article_provisos(review, condition_gate._law_articles)
        self.assertTrue(any("제7조 단서1" in g for g in got), got)


class OrdinanceDelegationTest(unittest.TestCase):
    """11/7 #1344 일조권: 개정 법 제61조 '…범위에서 조례로 정하는 거리 이상' — 제목·요약·첫 문단은 '5m 이상만 띄우면 돼요'."""
    REVIEW = [{"claim": "10~17m 부분 5m (건축법 제61조)", "verdict": "맞음", "action": "유지", "urls": ["https://www.law.go.kr/법령/건축법"]}]
    TEXT = ("① … 다음 각 호의 구분에 따른 범위에서 해당 지방자치단체의 조례로 정하는 거리 이상을 띄어 건축하여야 한다. "
            "1. 높이 10미터 이하인 부분: 1.5미터 이상 2. 높이 10미터 초과 17미터 이하인 부분: 5미터 이상")

    def run_check(self, lead):
        import datetime as dt
        from bot.condition_gate import ordinance_delegation
        return ordinance_delegation("일조권 사선제한 개정", "", f"<p>{lead}</p>", self.REVIEW,
                                    articles_of=lambda base: {}, changes_of=lambda n, a, t: [{"text": self.TEXT}], today=dt.date(2026, 11, 7))

    def test_missing_ordinance_flagged(self):
        self.assertEqual(len(self.run_check("10m 초과 17m 이하 부분은 5m 이상만 띄우면 돼요.")), 1)
        # 첫 문단이 위임 범위의 숫자를 말하지 않으면 막지 않음 (양성화 글 '이행강제금 5회분' 오탐)
        self.assertEqual(self.run_check("과태료는 이행강제금 5회분이에요."), [])

    def test_ordinance_mentioned_passes(self):
        self.assertEqual(self.run_check("법 기준은 5m 이상이고 실제 거리는 조례로 정해요."), [])


class HeadlineFromPriceTest(unittest.TestCase):
    """11/7 #1272: 제목 '응시료 75,000원부터' — 본문 첫 문단에 필기 25,000원."""

    def test_cheaper_in_body_flagged(self):
        from bot.condition_gate import headline_from_price
        body = "<p>2급은 필기 25,000원·실기 60,000원이에요.</p>"
        self.assertEqual(len(headline_from_price("BIM 자격증, 응시료 75,000원부터", "", body)), 1)

    def test_lowest_or_unrelated_passes(self):
        from bot.condition_gate import headline_from_price
        self.assertEqual(headline_from_price("응시료 25,000원부터", "", "<p>필기 25,000원·실기 60,000원</p>"), [])
        self.assertEqual(headline_from_price("숙박 70,000원부터", "", "<p>과태료 20,000원이에요. 1박 70,000원</p>"), [])


class SmallWorkplaceItemTest(unittest.TestCase):
    """11/7 #1265: 작은 회사도 된다는 임산부 단축근무 글에 '태아검진 시간 (제74조의2)'을 조건 없이 — 별표 1 밖 조문."""
    BODY = ("<p>근로기준법 제74조는 상시 4명 이하 사업장에도 적용돼요.</p>"
            "<ul><li>태아검진 시간: 청구하면 회사는 허용해야 해요 (제74조의2).</li></ul>")

    def test_excluded_article_flagged(self):
        from bot.condition_gate import small_workplace_items
        self.assertEqual(len(small_workplace_items(self.BODY)), 1)

    def test_condition_or_other_law_passes(self):
        from bot.condition_gate import small_workplace_items
        ok = self.BODY.replace("태아검진 시간:", "태아검진 시간: 상시 5명 이상 사업장이라면")
        self.assertEqual(small_workplace_items(ok), [])
        other = "<p>4명 이하 사업장도 돼요 (근로기준법).</p><li>육아기 단축은 남녀고용평등법 제19조의2 예요.</li>"
        self.assertEqual(small_workplace_items(other), [])

    def test_article_table(self):
        from bot.condition_gate import small_workplace_article
        self.assertTrue(small_workplace_article(74, 0))
        self.assertFalse(small_workplace_article(74, 2))
        self.assertTrue(small_workplace_article(43, 2))     # '제43조부터 제45조까지' 안의 가지 조문
        self.assertFalse(small_workplace_article(60, 0))    # 연차
        self.assertIsNone(small_workplace_article(23, 0))   # 항 단위 적용 — 판단 안 함


class OrdinaryWageRuleTest(unittest.TestCase):
    """11/7 #1232: '통상임금은 … 기본급과 매달 정해진 금액으로 나오는 수당이 중심' — 2024.12.19 전원합의체 뒤 옛 기준."""

    def test_old_rule_flagged(self):
        from bot.condition_gate import ordinary_wage_rule
        self.assertEqual(len(ordinary_wage_rule("<p>통상임금은 기본급과 매달 정해진 금액으로 나오는 수당이 중심이에요.</p>")), 1)
        self.assertEqual(len(ordinary_wage_rule("<p>통상임금은 정기성·일률성·고정성을 갖춰야 해요.</p>")), 1)

    def test_current_rule_passes(self):
        from bot.condition_gate import ordinary_wage_rule
        self.assertEqual(ordinary_wage_rule("<p>통상임금은 정기적·일률적으로 주기로 정한 임금이고, 재직 조건이 붙어도 빠지지 않아요.</p>"), [])
        self.assertEqual(ordinary_wage_rule("<p>월급은 매달 정해진 날에 받아요.</p>"), [])

    def test_in_gate(self):
        self.assertIn("condition_gate.ordinary_wage_rule(", (ROOT / "bot" / "review_gate.py").read_text(encoding="utf-8"))


class ApproxRoundingTest(unittest.TestCase):
    """11/6 #1212: 표 합계 34,428,900원을 첫 문단에 '약 3,442만원' (반올림 3,443만원)."""

    def test_truncated_flagged(self):
        from bot.condition_gate import approx_rounding
        self.assertEqual(len(approx_rounding("<p>연간 약 3,442만원이에요.</p><td>34,428,900원</td>")), 1)

    def test_rounded_passes(self):
        from bot.condition_gate import approx_rounding
        self.assertEqual(approx_rounding("<p>연간 약 3,443만원이에요.</p><td>34,428,900원</td>"), [])
        self.assertEqual(approx_rounding("<p>약 3,442만원</p><td>34,421,000원</td>"), [])

    def test_in_gate(self):
        self.assertIn("condition_gate.approx_rounding(", (ROOT / "bot" / "review_gate.py").read_text(encoding="utf-8"))


class QuotaResetTest(unittest.TestCase):
    """11/7: 9시 초기화 직후 한 번 '한도 소진'을 받자 다음 날까지 막혀 사진 채우기가 하루 멈춤."""

    def test_next_try(self):
        from datetime import datetime, timezone
        from bot.images import _next_try
        t = lambda h, m=0: datetime(2026, 11, 7, h, m, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(_next_try(t(0, 1)), t(0, 31))                                   # 초기화 직후 → 30분 뒤 다시
        self.assertEqual(_next_try(t(5)), datetime(2026, 11, 8, 0, 0, 5, tzinfo=timezone.utc))  # 진짜 소진 → 다음 초기화

    def test_old_full_day_block_is_lifted(self):
        import os
        import tempfile
        from datetime import datetime, timezone
        from bot import images
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "q")
            f.write_text("2026-11-08T00:00:05+00:00")                    # 예전 방식: 하루 통째로
            written = datetime(2026, 11, 7, 0, 0, 10, tzinfo=timezone.utc).timestamp()
            os.utime(f, (written, written))
            with mock.patch.object(images, "QUOTA_FILE", f):
                self.assertFalse(images.quota_blocked())                    # 00:00:10 에 썼으면 00:30:10 뒤엔 풀림 (지금은 그 뒤)


class CheckedLineTest(unittest.TestCase):
    """11/7 운영자 '확인 방식 한 줄 — 하자, AI 사용은 빼고' (first-lab-author-box.php)."""

    def run_php(self, record, dates):
        import os
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("php"):
            self.skipTest("php 없음")
        code = ("<?php define('ABSPATH', 1); function add_filter() {} function wp_parse_url($u, $c) { return parse_url($u, $c); }\n"
                f"require '{ROOT / 'wordpress' / 'mu-plugins' / 'first-lab-author-box.php'}';\n"
                "echo firstlab_checked_line(json_decode(getenv('R'), true), json_decode(getenv('D'), true));")
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(code)
        env = {**os.environ, "R": json.dumps(record), "D": json.dumps(dates)}
        return subprocess.run(["php", f.name], capture_output=True, text=True, check=True, env=env).stdout

    def test_line_from_record(self):
        rec = {"laws": [["https://www.law.go.kr/법령/근로기준법", "제55조"]],
               "found": [{"urls": ["https://www.moel.go.kr/a"]}, {"urls": ["https://museum.gunsan.go.kr/b", "https://www.law.go.kr/x"]}]}
        self.assertEqual(self.run_php(rec, ["2026-11-01", "", "2026-11-07 08:19:21"]),
                         "이 글은 법제처 법령 원문과 고용노동부·공공기관 공식 페이지로 확인했어요 · 마지막 확인 2026년 11월 7일")
        self.assertEqual(self.run_php({"laws": [["https://www.law.go.kr/법령/소득세법", "제12조"]]}, ["2026-11-03"]),
                         "이 글은 법제처 법령 원문으로 확인했어요 · 마지막 확인 2026년 11월 3일")
        self.assertEqual(self.run_php({"found": [{"urls": ["https://www.nhis.or.kr/a"]}]}, []), "이 글은 국민건강보험공단 공식 페이지로 확인했어요")
        self.assertEqual(self.run_php({}, ["2026-11-07"]), "")                                      # 기록 없으면 줄 없음
        self.assertEqual(self.run_php({"found": [{"urls": ["https://blog.naver.com/x"]}]}, []), "")   # 공식 출처 아님

    def test_reviewed_sites_included(self):
        """11/7 운영자 '고용노동부도 확인됐으면 같이 넣어주면 되는 거 아냐?': 검수에서 맞음으로 확인한 공식 사이트도 표시."""
        from bot import source_check
        review = [{"claim": "a", "verdict": "맞음", "action": "유지", "urls": ["https://1350.moel.go.kr/x", "https://www.law.go.kr/법령/근로기준법/제55조"]},
                  {"claim": "b", "verdict": "틀림", "action": "삭제", "urls": ["https://www.nts.go.kr/y"]},
                  {"claim": "c", "verdict": "맞음", "action": "유지", "urls": ["https://blog.naver.com/z"]}]
        self.assertEqual(source_check.reviewed_hosts(review), ["1350.moel.go.kr"])   # 틀림·비공식·법령(따로 laws)은 뺌
        rec = {"laws": [["https://www.law.go.kr/법령/근로기준법", "제55조"]], "reviewed": ["1350.moel.go.kr"]}
        self.assertEqual(self.run_php(rec, ["2026-11-07"]),
                         "이 글은 법제처 법령 원문과 고용노동부 공식 페이지로 확인했어요 · 마지막 확인 2026년 11월 7일")

    def test_backfill_old_records_once(self):
        from bot import source_check
        meta = {"5": json.dumps({"laws": [], "found": []})}

        def wp(*a):
            if a[0] == "eval":
                return json.dumps([{"id": 5, "slug": "주휴수당뜻"}] if "reviewed" not in meta["5"] else [])
            if a[:3] == ("post", "meta", "get"):
                return meta[a[3]]
            if a[:3] == ("post", "meta", "update"):
                meta[a[3]] = a[5]
            return ""
        review = [{"claim": "a", "verdict": "맞음", "action": "유지", "urls": ["https://www.moel.go.kr/x"]}]
        msg = source_check.backfill_reviewed(wp=wp, review_of=lambda slug: review if slug == "주휴수당뜻" else [])
        self.assertIn("5(1)", msg)
        self.assertEqual(json.loads(meta["5"])["reviewed"], ["moel.go.kr"])
        self.assertIsNone(source_check.backfill_reviewed(wp=wp, review_of=lambda s: review))   # 다 채우면 할 일 없음

    def test_no_ai_wording(self):
        src = (ROOT / "wordpress" / "mu-plugins" / "first-lab-author-box.php").read_text(encoding="utf-8")
        self.assertNotIn("AI", src.split("*/", 1)[1])   # 운영자: AI 사용 문구는 빼기


class SiteFocusTest(unittest.TestCase):
    """11/7 운영자 '주제 좁히기·겹치는 글 합치기' 승인."""

    def test_focus_and_off_topic(self):
        from bot.select_keyword import off_topic, on_focus
        self.assertTrue(on_focus("실업급여신청서류"))
        self.assertTrue(on_focus("퇴직금계산기"))
        self.assertFalse(on_focus("인천공항주차요금"))
        for kw in ("블루베리보관법", "냉장고정리방법", "캠핑준비물리스트", "김치찌개레시피"):
            self.assertTrue(off_topic(kw), kw)   # 후순위 표시 (빼지는 않음)
        self.assertFalse(off_topic("실업급여신청"))

    def test_seed_words_focus_first_others_later(self):
        """중심 주제 씨앗 먼저, 빼지 않고 뒤에 (11/7 운영자 '빼지 말고 후순위 — 황금 키워드 나올 수도')."""
        from bot.seed_words import FOCUS_SEEDS, SEED_WORDS
        from bot.select_keyword import on_focus
        self.assertEqual(SEED_WORDS[:len(FOCUS_SEEDS)], list(FOCUS_SEEDS))
        for w in ("레시피", "에어프라이어", "스킨케어", "오사카", "캠핑장"):
            self.assertGreater(SEED_WORDS.index(w), len(FOCUS_SEEDS))
        self.assertTrue(on_focus("청년도약계좌조건"))
        self.assertTrue(on_focus("경정청구기간"))
        src = (ROOT / "bot" / "select_keyword.py").read_text(encoding="utf-8")
        self.assertIn('(-x["tier"], not x["later"], x["info"], x["score"])', src)
        self.assertNotIn("off_topic(r[\"norm\"]) or", src)   # 후보에서 빼지 않음

    def test_focus_snowball_grows_seeds(self):
        """창고의 중심 주제 키워드가 다음 수집 출발 단어로 — 쉰 지 14일 안 된 것·중심 주제 아닌 것·검색 적은 것은 뺌."""
        import sqlite3
        from bot import discover
        from bot.keyword_db import SCHEMA
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        rows = [("실업급여조건", 9000), ("육아휴직급여신청", 4000), ("캠핑장예약", 8000), ("경정청구방법", 100), ("연말정산환급", 7000)]
        for kw, total in rows:
            conn.execute("INSERT INTO keywords(keyword, norm, total, first_seen, last_checked) VALUES (?,?,?,?,?)", (kw, kw, total, "2026-11-01", "2026-11-07"))
        conn.execute("INSERT INTO seeds(word, used_date) VALUES ('연말정산환급', '2026-11-01')")   # 6일 전에 씀 → 아직 쉼
        self.assertEqual(discover.focus_snowball(conn, "2026-11-07", 10), ["실업급여조건", "육아휴직급여신청"])
        self.assertEqual(discover.focus_snowball(conn, "2026-11-20", 10), ["실업급여조건", "연말정산환급", "육아휴직급여신청"])

    def test_same_topic_with_qualifier(self):
        """11/7 #1559·#1561: '미사용 연차수당 계산'과 '연차수당 계산'을 두 칸이 동시에 씀 — 같은 주제로 보고 같은 칸·나중 글 보류."""
        from bot import freshness
        from bot.select_keyword import topic_key
        self.assertEqual(topic_key("미사용연차수당계산"), topic_key("연차수당계산"))
        self.assertNotEqual(topic_key("연차수당계산"), topic_key("연차발생기준"))
        self.assertEqual(topic_key("내일배움카드신청")[0], "내일배움카드")   # '내' 로 시작해도 안 뗌
        got = freshness.same_topic(1561, "연차수당계산", {1559: "미사용연차수당계산", 1561: "연차수당계산"})
        self.assertIn("#1559", got[0])
        self.assertEqual(freshness.same_topic(1559, "미사용연차수당계산", {1559: "미사용연차수당계산", 1561: "연차수당계산"}), [])  # 먼저 쓴 글은 그대로
        api = (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8")
        self.assertIn("$c['topic'] ?? $c['keyword']", api)
        self.assertIn("같은 주제 글", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))

    def test_template_twin_held_but_first_and_rewritten_pass(self):
        """검사소 연작: 먼저 쓴 글은 그대로, 같은 틀로 찍은 뒤 글은 보류, 이 글만의 정보로 다시 쓰면 통과."""
        from bot import freshness
        common = "".join(f"<p>정기검사 수수료는 승용차 23,000원이고 종합검사는 지역에 따라 달라요 {i}번 안내예요.</p>" for i in range(30))
        first = "<p>노원검사소는 하계동에 있어요.</p>" + common
        copy = "<p>성동검사소는 송정동에 있어요.</p>" + common
        got = freshness.template_twin(20, copy, {10: first})
        self.assertEqual(len(got), 1)
        self.assertIn("#10", got[0])
        rewritten = ("<p>성동검사소는 송정동 동부간선도로 옆에 있어요. 전화 02-000-0000, 화물차 검사 라인이 따로 있어요.</p>"
                     + "".join(f"<p>성동 검사소 {i}번 출입구는 송정로 쪽이고 주말 대기 줄이 길어요.</p>" for i in range(30))
                     + "<p>요금·과태료는 정기검사 비용 글을 보세요.</p>")
        self.assertEqual(freshness.template_twin(20, rewritten, {10: first}), [])
        self.assertEqual(freshness.template_twin(20, "<p>짧은 글</p>", {10: first}), [])   # 너무 짧으면 판단 안 함
        self.assertIn("같은 틀 글", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))


class HolidayAttendanceTest(unittest.TestCase):
    """11/7 #1601: 요약·첫 문단 '주 15시간 이상이면 주휴수당' — 본문엔 있는 '개근' 요건이 빠짐 (시행령 제30조①)."""

    def test_flag_and_pass(self):
        from bot.condition_gate import holiday_attendance
        body = "<p>알바 급여는 1주 15시간 이상이면 주휴수당을 더해요.</p><p>그 주 소정근로일을 개근해야 해요.</p>"
        self.assertEqual(len(holiday_attendance("주 15시간 이상이면 주휴수당을 더해요.", body)), 1)
        self.assertEqual(holiday_attendance("주 15시간 이상 일하고 그 주 소정근로일을 다 나오면 주휴수당을 더해요.", body), [])
        self.assertEqual(holiday_attendance("시급 계산법", "<p>시급 계산</p>"), [])


class LaborDayExceptionTest(unittest.TestCase):
    """11/7 #1607: '무급휴무일과 겹친 공휴일은 유급휴일수당 없음' — 노동절은 예외 (노동절 제정에 관한 법률, 고용노동부 상담)."""

    def test_flag_and_pass(self):
        from bot.condition_gate import labor_day_exception
        bad = "<p>무급휴무일과 공휴일이 겹치면 따로 정한 게 없는 한 유급휴일수당은 없어요.</p>"
        self.assertEqual(len(labor_day_exception(bad)), 1)
        good = "<p>무급휴무일과 공휴일이 겹치면 유급휴일수당은 없어요. 노동절(5월 1일)은 예외예요.</p>"
        self.assertEqual(labor_day_exception(good), [])


class OtherIncomeTaxScopeTest(unittest.TestCase):
    """11/7 #1630: '그 밖의 기타소득' 선택지에 복권(200만 원 비과세)을 넣으면 33만 원이 나옴 — 계산 대상 밖을 선택지·안내로 막음."""

    def test_option_scope(self):
        src = (ROOT / "wordpress" / "themes" / "first-lab" / "calculators" / "defs" / "other-income-tax.js").read_text(encoding="utf-8")
        self.assertNotIn("그 밖의 기타소득", src)
        self.assertIn("복권·연금계좌 연금외수령", src)


class DeletedArticleTransitionTest(unittest.TestCase):
    """11/7 #1673: 삭제된 조문(조특법 제86조)은 부칙 경과조치로 아직 적용될 수 있음 — lawtext 가 함께 보여 줌."""

    def test_transition_found(self):
        from bot.lawtext import deleted_transitions
        raw = ("<![CDATA[제40조(개인연금저축에 대한 경과조치) 2013년 1월 1일 전에 종전의 제86조에 따라 가입한 개인연금저축은 "
               "제86조 및 제89조의2의 개정규정에도 불구하고 종전의 규정을 적용한다.]]><![CDATA[제41조(연금저축) 종전의 제86조의2 …]]>")
        got = deleted_transitions(raw, "제86조")
        self.assertEqual(len(got), 1)
        self.assertIn("개인연금저축", got[0])


class NoticeYearLabelTest(unittest.TestCase):
    """11/7 #1678: 2026년 공고 값(연간 7천만 원)을 제목·요약이 연도 없이 — 한 회차 규칙을 일반 규칙처럼 (하루 세 번째)."""

    def test_flag_and_pass(self):
        from bot.condition_gate import notice_year_label
        body = "<p>2026년 공고 기준이에요.</p><p>2026년 소상공인 정책자금 융자계획에 따라요.</p>"
        self.assertEqual(len(notice_year_label("정책자금: 연간 7천만 원", "기준금리+0.6%p", body)), 1)
        self.assertEqual(notice_year_label("정책자금: 연간 7천만 원(2026년 공고)", "", body), [])
        self.assertEqual(notice_year_label("엑셀 자격증 22,000원", "", "<p>2027년 9월 1일부터 채용 공고 가점이 바뀌어요.</p>"), [])


class ReviewRatesTest(unittest.TestCase):
    """11/7 #1752: 검수자가 한국은행 사이트를 못 열어 4.96%를 확인불가로 — 서버 ECOS 값을 검수 파일에 넣음."""

    def test_rates_only_for_rate_posts(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("unreviewed", ROOT / "automation" / "unreviewed.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rates = {"household": {"month": "202609", "value": 4.96}}
        self.assertEqual(mod.bok_rates({"content_html": "<p>가계대출 금리 4.96%</p>"}, rates), rates)
        self.assertIsNone(mod.bok_rates({"content_html": "<p>연차수당 계산</p>"}, rates))
        self.assertIsNone(mod.bok_rates({"content_html": "<p>금리</p>"}, None))
        self.assertIn("bok_rates", (ROOT / "automation" / "review-prompt.md").read_text(encoding="utf-8"))


class YearOnlyHeadlineTest(unittest.TestCase):
    """11/7 #1929 제목 '3억이면 299,400원' — 본문은 '2026년분 재산세에 한해' 비율이라 다음 해엔 틀린 제목."""

    def test_flag_and_pass(self):
        from bot.condition_gate import year_only_headline
        body = "<p>1세대 1주택 43~45%는 시행령이 2026년분 재산세에 한해 정한 비율이에요.</p>"
        self.assertEqual(len(year_only_headline("재산세 계산기: 3억이면 299,400원", "", body)), 1)
        self.assertEqual(year_only_headline("재산세 계산기: 2026년 3억이면 299,400원", "", body), [])
        self.assertEqual(year_only_headline("재산세 계산 방법", "", body), [])


class LawAbsenceTableTest(unittest.TestCase):
    """11/7 #1901 표 칸 '법률 근거 없음' — 다른 법률 전체는 확인 안 한 부정 단정."""

    def test_table_cell(self):
        from bot.condition_gate import law_absence
        self.assertEqual(len(law_absence("<table><tr><td>재직 경찰 검사</td><td>법률 근거 없음</td></tr></table>")), 1)
        self.assertEqual(law_absence("<table><tr><td>재직 경찰 검사</td><td>경찰공무원법에 조항 없음</td></tr></table>"), [])


class AverageWageFloorTest(unittest.TestCase):
    """11/7 #1858: 평균임금 = 3개월 임금 ÷ 총일수만 쓰고 통상임금 보장(근로기준법 제2조②) 누락."""

    def test_flag_and_pass(self):
        from bot.condition_gate import average_wage_floor
        body = "<p>1일 평균임금은 퇴직일 이전 3개월 동안 받은 임금 총액을 그 기간의 총일수로 나눈 금액이에요.</p>"
        self.assertEqual(len(average_wage_floor(body)), 1)
        self.assertEqual(average_wage_floor(body + "<p>이 금액이 1일 통상임금보다 적으면 통상임금으로 계산해요.</p>"), [])
        self.assertEqual(average_wage_floor("<p>연차수당은 통상임금으로 계산해요.</p>"), [])


class ReadabilityTest(unittest.TestCase):
    """11/7 운영자 '글 가독성은 체크 안 하나': 목록 항목 200자·문단 300자·문장 100자, 법 조문 괄호 1개까지."""

    def test_element_limits(self):
        from bot.condition_gate import readability_element
        self.assertEqual(readability_element("li", "짧은 항목이에요. 괜찮아요."), [])
        self.assertTrue(any("목록 항목" in x for x in readability_element("li", "가나다라마. " * 40)))
        self.assertTrue(any("문장이" in x for x in readability_element("p", "가" * 101 + ".")))
        self.assertEqual(readability_element("p", "가" * 99 + "."), [])
        two = "규정이 있어요(근로기준법 제60조). 예외도 있어요(가사근로자법 제16조)."
        self.assertTrue(any("법 조문 괄호" in x for x in readability_element("li", two)))

    def test_sentences_keep_parentheses(self):
        from bot.condition_gate import split_sentences
        self.assertEqual(split_sentences("가요(제1항. 다만 예외). 나요. 다요"), ["가요(제1항. 다만 예외).", "나요.", "다요"])
        self.assertEqual(split_sentences("비율은 0.07%예요. 끝."), ["비율은 0.07%예요.", "끝."])

    def test_body_skips_nested_lists(self):
        from bot.condition_gate import readability
        self.assertEqual(readability("<ul><li>짧아요<ul><li>하위</li></ul></li></ul><p>문단이에요.</p>"), [])
        self.assertEqual(len(readability("<p>" + "가나다. " * 80 + "</p>")), 1)

    def test_review_patch_blocked_when_long(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        post = {"content_html": "<ul><li>원래 항목이에요.</li></ul>", "title": "t", "excerpt": "e"}
        long_fix = [{"type": "missing_condition", "why": "w", "find": "원래 항목", "tag": "li",
                     "replace_paragraph": "고친 항목이에요. " * 30, "prevention": "review_only:x"}]
        self.assertTrue(any("읽기" in e for e in apply_review.check(post, long_fix)))

    def test_display_split_php(self):
        import os
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("php"):
            self.skipTest("php 없음")
        li = ("<li><strong>상시 4명 이하</strong>: 적용되지 않아요. 동거 친족만 쓰는 사업장은 근로기준법이 적용되지 않아요. "
              "다만 인증 기관과 계약한 가사근로자는 적용돼요. 이때 연차는 실제 근로시간 기준이고 15시간 미만이면 생기지 않아요 "
              "(가사근로자의 고용개선 등에 관한 법률 제16조). 그래서 이 계산기 결과와 다를 수 있으니 계약서와 근로시간 기록을 챙겨 고용노동부 고객상담센터에 꼭 확인해 보세요.</li>")
        code = ("<?php define('ABSPATH', 1);\n"
                f"require '{ROOT / 'wordpress' / 'mu-plugins' / 'first-lab-readability.php'}';\n"
                "echo firstlab_read_list_item(getenv('LI'));")
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(code)
        out = subprocess.run(["php", f.name], capture_output=True, text=True, check=True, env={**os.environ, "LI": li}).stdout
        self.assertIn('<ul class="fl-split">', out)
        self.assertIn("<li>다만 인증 기관", out)                       # 예외 문장은 따로
        self.assertIn('<span class="fl-ref">(가사근로자의', out)        # 근거 괄호는 작게
        self.assertEqual(re.sub(r"<[^>]+>", "", out).replace(" ", ""), re.sub(r"<[^>]+>", "", li).replace(" ", ""))  # 글자는 그대로


class ExcerptReduceTest(unittest.TestCase):
    """11/7 #1766 요약 '가산세 50%가 줄어요' ↔ 본문 '감면을 못 받는 경우' — '받아요'만 보던 검사가 놓침."""

    def test_reduce_promise(self):
        from bot.condition_gate import excerpt_vs_exclusions
        body = "<h2>감면을 못 받는 경우</h2><p>조사 착수를 알고 낸 경우</p>"
        self.assertEqual(len(excerpt_vs_exclusions("1개월 안이면 무신고가산세 50%가 줄어요.", body)), 1)
        self.assertEqual(excerpt_vs_exclusions("1개월 안이면 50%가 줄어요(조사 착수를 알고 낸 경우는 감면 제외).", body), [])


class CountyScopeTest(unittest.TestCase):
    """11/7 #1734·#1759: 중과 제외 3억 원 이하 주택 지역을 '광역시 군·읍·면'으로 좁혀 씀."""

    def test_flag_and_pass(self):
        from bot.condition_gate import county_scope
        self.assertEqual(len(county_scope("<p>수도권·광역시 밖(광역시 군·읍·면 포함)에 있는 기준시가 3억 원 이하 주택은 빠져요.</p>")), 1)
        self.assertEqual(len(county_scope("<p>세종 밖(광역시에 속한 군과 읍·면 지역도 포함)의 기준시가 3억 원 이하 주택</p>")), 1)
        self.assertEqual(county_scope("<p>밖(광역시의 군 지역과 수도권·광역시·세종의 읍·면 지역도 포함)의 기준시가 3억 원 이하 주택</p>"), [])
        self.assertEqual(county_scope("<p>광역시 군·읍·면 지역 농어촌주택 특례</p>"), [])


class UnresolvedDelegationTest(unittest.TestCase):
    """11/7 #1705 '신고일부터 4주 범위에서 대통령령으로 정한 기간' — 시행령 제71조의2는 '2주'."""

    def test_flag_and_pass(self):
        from bot.condition_gate import unresolved_delegation
        self.assertEqual(len(unresolved_delegation("<p>대기기간이 7일이 아니라 신고일부터 4주 범위에서 대통령령으로 정한 기간이에요.</p>")), 1)
        self.assertEqual(len(unresolved_delegation("<p>승진은 대통령령으로 정한 비율에 따라 해요.</p>")), 1)
        self.assertEqual(unresolved_delegation("<p>대기기간은 신고일부터 2주예요.</p>"), [])
        self.assertEqual(unresolved_delegation("<p>2027년 6월 10일부터 대통령령으로 정하는 기간 안에 신고해요.</p>"), [])
        self.assertEqual(unresolved_delegation("<p>시행규칙으로 정하는 기간은 아직 나오지 않았어요.</p>"), [])
        self.assertEqual(unresolved_delegation("<p>대통령령으로 정하는 시간 단위·일수 범위에서 나눠 써요.</p>"), [])


class RetentionStartTest(unittest.TestCase):
    """11/6 #1208 '계약서는 사업주가 3년간 보존' — 시행령 제22조②: 근로관계가 끝난 날부터 3년."""

    def test_missing_start_flagged(self):
        from bot.condition_gate import retention_start
        self.assertEqual(len(retention_start("<p>계약서는 사업주가 3년간 보존해야 해요.</p>")), 1)
        self.assertEqual(len(retention_start("<p>임금대장(3년 보존)이에요.</p>")), 1)

    def test_with_start_passes(self):
        from bot.condition_gate import retention_start
        self.assertEqual(retention_start("<p>근로관계가 끝난 날부터 3년간 보존해야 해요.</p>"), [])
        self.assertEqual(retention_start("<p>확정신고 기한 후 5년간 보존해야 해요.</p>"), [])
        self.assertEqual(retention_start("<p>전송하면 5년 보존 의무도 없어요.</p>"), [])

    def test_retention_table_column(self):
        """11/7 #1428: '보관 기준' 열에 '임금채권 시효 3년' — 문장에 '보관' 말이 없어도 표 머리칸으로 봄."""
        from bot.condition_gate import retention_start
        table = ("<table><tr><th>서류</th><th>보관 기준</th></tr>"
                 "<tr><td>근로계약서</td><td>{}</td></tr></table>")
        self.assertEqual(len(retention_start(table.format("임금채권 시효 3년"))), 1)
        self.assertEqual(retention_start(table.format("임금을 받을 수 있는 때부터 3년(시효에 맞춘 권고)")), [])
        self.assertEqual(retention_start("<table><tr><th>서류</th><th>금액</th></tr><tr><td>a</td><td>3년 약정</td></tr></table>"), [])

    def test_in_gate(self):
        self.assertIn("condition_gate.retention_start(", (ROOT / "bot" / "review_gate.py").read_text(encoding="utf-8"))


class StaleFormTest(unittest.TestCase):
    """11/6 #1180: 2020년 판 이직확인서 파일(flSeq=94608429)을 근거로 써 소정근로시간 선택지·이직코드가 2025.7.1 개정 전 내용."""
    URL = "https://www.law.go.kr/LSW/flDownload.do?flSeq=94608429"

    def test_form_head_parsed(self):
        import datetime as dt
        from bot.law_api import form_heads
        got = form_heads("■ 고용보험법 시행규칙[별지 제75호의4서식] <개정 2020. 8. 28.> 피보험자 이직확인서 "
                         "■ 고용보험법 시행규칙 [별표 2] <개정 2019.1.1, 2024. 7. 1.> "
                         "■ 고용보험법 시행규칙[별지 제8호서식] [별지 제75호의4서식으로 이동 <2020. 8. 28.>]")
        self.assertEqual(got, {"고용보험법시행규칙|별지75의4": dt.date(2020, 8, 28), "고용보험법시행규칙|별표2": dt.date(2024, 7, 1)})

    def test_old_form_blocked(self):
        from bot import condition_gate
        review = [{"claim": "이직코드 23 (고용보험법 시행규칙 별지 제75호의4서식)", "urls": [self.URL]}]
        got = condition_gate.stale_forms(review, lambda u: {"고용보험법시행규칙|별지75의4": "2020-08-28"},
                                         lambda law: {"고용보험법시행규칙|별지75의4": "2025-07-01"})
        self.assertEqual(len(got), 1)
        self.assertIn("2025-07-01", got[0])

    def test_current_or_unreadable_passes(self):
        from bot import condition_gate
        review = [{"claim": "c", "urls": [self.URL]}]
        cur = lambda law: {"고용보험법시행규칙|별지75의4": "2025-07-01"}  # noqa: E731
        self.assertEqual(condition_gate.stale_forms(review, lambda u: {"고용보험법시행규칙|별지75의4": "2025-07-01"}, cur), [])
        self.assertEqual(condition_gate.stale_forms(review, lambda u: {}, cur), [])
        self.assertEqual(condition_gate.stale_forms(review, lambda u: {"고용보험법시행규칙|별지75의4": "2020-08-28"}, lambda law: {}), [])

    def test_in_server_gate_and_apply_review(self):
        self.assertIn("condition_gate.stale_forms(", (ROOT / "bot" / "review_gate.py").read_text(encoding="utf-8"))
        self.assertIn('"stale_forms":', (ROOT / "automation" / "apply_review.py").read_text(encoding="utf-8"))


class UpcomingAmendmentTest(unittest.TestCase):
    """근거 조문이 곧 바뀌는데 지금 규칙만 씀 (11/6 #1102 보험료징수법 제16조의10 — 2027-01-01 단서 변경)."""

    REVIEW = [{"claim": "월 60시간 미만은 신고 안 해도 돼요 (고용보험및산업재해보상보험의보험료징수등에관한법률 제16조의10)",
               "verdict": "맞음", "action": "유지", "urls": ["https://www.law.go.kr/법령/고용보험및산업재해보상보험의보험료징수등에관한법률/제16조의10"]}]

    def test_soon_change_must_be_mentioned(self):
        import datetime as dt
        from bot.condition_gate import upcoming_amendments
        today = dt.date(2026, 11, 6)
        soon = lambda name, art, t: [{"date": dt.date(2027, 1, 1), "text": "소득기준 미만"}]  # noqa: E731
        self.assertEqual(len(upcoming_amendments(self.REVIEW, "<p>월 60시간 미만은 신고 안 해도 돼요.</p>", soon, today)), 1)
        self.assertEqual(upcoming_amendments(self.REVIEW, "<p>2027년 1월부터는 소득기준 미만으로 바뀌어요.</p>", soon, today), [])
        noted = self.REVIEW + [{"claim": "제16조의10 시행 예정 해당 없음 — 이 글은 2026년 신고만", "verdict": "맞음", "action": "유지", "urls": []}]
        self.assertEqual(upcoming_amendments(noted, "<p>x</p>", soon, today), [])
        far = lambda name, art, t: [{"date": dt.date(2028, 1, 1), "text": "x"}]  # noqa: E731
        self.assertEqual(upcoming_amendments(self.REVIEW, "<p>x</p>", far, today), [])   # 6개월 넘게 남은 개정은 공개 직전 재검수 몫

    def test_wired(self):
        self.assertIn("upcoming_amendments", (ROOT / "bot" / "review_gate.py").read_text(encoding="utf-8"))


class JsonListMergeTest(unittest.TestCase):
    """패치 목록(JSON)을 감시 세션·글쓰기 작업이 동시에 더해도 충돌 없이 합침 (11/6 patches.json 충돌로 push 실패)."""

    def test_git_merge_with_driver(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            run = lambda *a: subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True)  # noqa: E731
            run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
            run("config", "merge.jsonlist.driver", f"python3 {ROOT / 'automation' / 'merge_json_list.py'} %O %A %B")
            (Path(d) / ".gitattributes").write_text((ROOT / ".gitattributes").read_text(encoding="utf-8"), encoding="utf-8")
            (Path(d) / "wordpress").mkdir()
            f = Path(d) / "wordpress" / "patches.json"
            f.write_text('[\n {"post": 1}\n]\n', encoding="utf-8"); run("add", "-A"); run("commit", "-qm", "base")
            run("checkout", "-qb", "other"); f.write_text('[\n {"post": 1},\n {"post": 3}\n]\n', encoding="utf-8"); run("commit", "-qam", "o")
            run("checkout", "-q", "main"); f.write_text('[\n {"post": 1},\n {"post": 2}\n]\n', encoding="utf-8"); run("commit", "-qam", "m")
            run("merge", "-q", "--no-edit", "other")
            self.assertEqual([x["post"] for x in json.loads(f.read_text(encoding="utf-8"))], [1, 2, 3])

    def test_wired(self):
        self.assertIn("merge.jsonlist.driver", (ROOT / "automation" / "check_and_push.sh").read_text(encoding="utf-8"))
        self.assertIn("merge.jsonlist.driver", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))


class DomesticWorkerLawTest(unittest.TestCase):
    """'가사근로자는 근로기준법 제17조가 적용되지 않아요'만 쓰고 가사근로자법 제14조(서면 교부)·제26조(벌금)를 뺌 (11/6 #1036)."""

    def test_replacement_law_needed(self):
        from bot.condition_gate import cross_law_exceptions
        bad = "<p>인증기관과 계약한 가사근로자는 가사 사용인으로 보지 않지만, 근로기준법 제17조는 적용되지 않아요.</p>"
        self.assertTrue(any("가사근로자법 제14조" in x for x in cross_law_exceptions(bad)))
        good = bad.replace("</p>", " 대신 가사근로자법 제14조에 따라 서면을 교부해야 해요.</p>")
        self.assertEqual(cross_law_exceptions(good), [])


class PatchOpenTagTest(unittest.TestCase):
    """찾을 문구에 여는 태그(<p>)를 넣으면 서버가 문단을 못 찾아 고친 내용이 안 들어감 (11/6 #1028 patch_problems)."""

    def test_open_tag_rejected_and_server_cannot_apply(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        from bot import fix_posts
        body = "<p>평일에는 공연이 14:30 한 번뿐이라 시간이 맞아요.</p>"
        bad = {"type": "missing_condition", "why": "x", "find": "<p>평일에는 공연이", "tag": "p",
               "replace_paragraph": "11~12월 평일 공연", "prevention": "review_only:x"}
        post = {"title": "t", "excerpt": "e", "content_html": body}
        self.assertTrue(any("여는 태그" in e for e in apply_review.check(post, [bad])))
        self.assertIsNone(fix_posts.patch_content(body, bad))   # 서버는 실제로 못 찾음 — 그래서 미리 막음
        ok = dict(bad, find="평일에는 공연이")
        self.assertIn("11~12월", fix_posts.patch_content(body, ok))


class UnverifiedPlainFragmentTest(unittest.TestCase):
    """확인 못 한 문구(숫자 없음)를 완화할 때, 같은 문단에 확인된 숫자가 있어도 숫자를 그대로 두면 review_only 허용 (11/6 #1150)."""

    def test_numbers_kept(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review as a
        el = "<p>HRD-Net에서 접수할 수 있어요. 교육비는 300,000원이에요.</p>"
        self.assertTrue(a._numbers_kept({"find": "HRD-Net에서 접수할 수 있어요",
                                         "replace_paragraph": "접수 방법은 공고에서 확인해요. 교육비는 300,000원이에요."}, el))
        self.assertFalse(a._numbers_kept({"find": "HRD-Net에서 접수할 수 있어요", "replace_paragraph": "접수 방법은 공고에서 확인해요."}, el))
        self.assertFalse(a._numbers_kept({"find": "교육비는 300,000원이에요", "replace_paragraph": "교육비는 공고에서 확인"}, el))


class QualityLogTest(unittest.TestCase):
    """외부 검수에서 고친 곳 수를 글마다 남김 — 동시 글쓰기 전후 품질 비교 (11/6 운영자 '퀄리티는 유지하거나 더 올리고')."""

    def test_records_fix_count_and_types(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "q.csv"
            apply_review.record_quality(1001, [{"type": "missing_condition"}, {"type": "wrong_fact"},
                                               {"type": "missing_condition"}], path, today="2026-11-06")
            apply_review.record_quality(1002, [], path, today="2026-11-07")
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "date,post,fixes,types")
        self.assertEqual(lines[1], "2026-11-06,1001,3,missing_condition:2 wrong_fact:1")
        self.assertEqual(lines[2], "2026-11-07,1002,0,")

    def test_apply_review_writes_it(self):
        self.assertIn("record_quality(post_id, items)", (ROOT / "automation" / "apply_review.py").read_text(encoding="utf-8"))


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
        self.assertTrue(any("못 찾음" in e for e in self.errs(prevention="check:title_has_keyword")))  # 검사 모듈 밖 함수 (11/5 #847)

    def test_named_gate_must_fire_on_original(self):
        self.assertEqual(self.errs(prevention="check:lead_vs_body"), [])
        self.post["content_html"] = "<p>반대로 성인이 맞는 Tdap은 국가 지원 대상이 아니라 병원마다 금액이 달라요.</p>"
        self.assertTrue(any("안 걸림" in e for e in self.errs(prevention="check:lead_vs_body")))

    def test_review_only_only_for_uncodeable_types(self):
        self.assertEqual(self.errs(prevention="review_only:어떤 예외가 빠졌는지는 법령 전체를 읽어야 앎"), [])
        self.assertTrue(any("너무 짧음" in e for e in self.errs(prevention="review_only:몰라")))
        self.assertTrue(any("코드 검사가 있는 유형" in e for e in self.errs(type="headline", prevention="review_only:제목이 본문과 다름을 고침")))

    def test_unverified_review_only_only_without_numbers(self):
        """근거 대조는 숫자만 페이지와 맞춰 봄 → 숫자 없는 단정(11/6 #874 '공항버스 제외')만 review_only, 숫자 주장은 검사로."""
        self.post["content_html"] = "<li>KTX·SRT, 시외·고속버스, 공항버스처럼 표를 따로 사는 수단은 빠져요</li><p>청년은 월 15회 이상 타면 30% 환급돼요.</p>"
        why = "review_only:숫자 없는 단정은 근거 대조가 페이지와 맞춰 보지 못함"
        self.assertEqual(self.errs(type="unverified", tag="li", find="공항버스처럼", replace_paragraph="KTX·SRT는 빠져요", prevention=why), [])
        self.assertTrue(any("코드 검사가 있는 유형" in e for e in
                            self.errs(type="unverified", find="30% 환급", replace_paragraph="자주 타면 환급돼요.", prevention=why)))


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

    def _publish_pick(self, rows, prio, pending=()):
        import json as _json
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot import freshness, naver_hub, review_queue, schedule
        calls = []

        def wp(*args):
            calls.append(args)
            if args[:2] == ("post", "list"):
                return _json.dumps(rows) if "--format=json" in args else "\n".join(str(r["ID"]) for r in rows)
            if args[:3] == ("post", "meta", "get") and args[4] == "_firstlab_img_pending":
                return '[{"prompt": "x"}]' if int(args[3]) in pending else ""
            return "글" if "--field=post_title" in args else ""
        with mock.patch.object(schedule, "wp_cli", side_effect=wp), \
             mock.patch.object(naver_hub, "publish_priority", return_value=prio), \
             mock.patch.object(review_queue, "reviewed_ids", return_value={r["ID"] for r in rows}), \
             mock.patch.object(schedule.seasons, "rank", return_value=schedule.seasons.NONE), \
             mock.patch.object(freshness, "check_post", return_value=[]):
            schedule.publish_next(datetime(2026, 11, 6, 8, 31, tzinfo=ZoneInfo("Asia/Seoul")))
        return int(next(c for c in calls if c[:2] == ("post", "update"))[2])

    def test_old_means_waited_21_days_not_queue_position(self):
        """11/6: '대기열 뒤 21편 말고 전부 오래된 글'로 세서 하루 30편씩 쌓이자 황금 점수가 거의 안 쓰임 → 쓴 날짜로 21일."""
        recent = [{"ID": 100 + i, "post_name": f"k{i}", "post_title": "t", "post_date": "2026-11-05 10:00:00"} for i in range(30)]
        self.assertEqual(self._publish_pick(recent, {129: 9.0}), 129)                       # 모두 하루 안 된 글 → 황금 점수 높은 글
        old = [{"ID": 50, "post_name": "o", "post_title": "t", "post_date": "2026-10-10 10:00:00"}]
        self.assertEqual(self._publish_pick(old + recent, {129: 9.0}), 50)                  # 21일 넘게 기다린 글은 먼저

    def test_publish_tiers_gold_focus_good_rest(self):
        """11/7 운영자 '주제도 아니고 황금도 아닌 글은 밀려도 된다': 황금 → 중심 주제 → 좋음 → 나머지 (21일 넘은 글도 무리 안에서만 먼저)."""
        from bot import naver_hub
        rows = [{"ID": 10, "post_name": "마라도여객선", "post_title": "t", "post_date": "2026-10-01 10:00:00"},   # 좋음, 36일 기다림
                {"ID": 11, "post_name": "수습기간해고", "post_title": "t", "post_date": "2026-11-05 10:00:00"},   # 중심 주제
                {"ID": 12, "post_name": "캠핑장예약", "post_title": "t", "post_date": "2026-11-05 10:00:00"}]
        labels = {10: "좋음", 11: "", 12: ""}
        with mock.patch.object(naver_hub, "publish_labels", return_value=labels):
            self.assertEqual(self._publish_pick(rows, {10: 9.0}), 11)
        labels[12] = "황금"
        with mock.patch.object(naver_hub, "publish_labels", return_value=labels):
            self.assertEqual(self._publish_pick(rows, {10: 9.0}), 12)
        rows[0]["post_date"] = "2026-07-01 10:00:00"   # 90일 넘게 기다린 글은 중심 주제 무리로 (영영 밀리지 않게)
        labels[12] = ""
        with mock.patch.object(naver_hub, "publish_labels", return_value=labels):
            self.assertEqual(self._publish_pick(rows, {10: 9.0}), 10)

    def test_writing_tier(self):
        from bot.select_keyword import tier
        self.assertEqual([tier("황금", "캠핑장예약"), tier("", "수습기간해고"), tier("좋음", "마라도여객선"), tier("", "캠핑장예약")], [0, 1, 2, 3])
        self.assertEqual(tier("좋음", "퇴직연금중도인출"), 1)

    def test_photo_pending_post_waits_its_turn(self):
        """11/6 운영자 '점수 높은 것대로 줄 세우고 그것들 먼저 사진 줘야지' + 11/9 '완벽하게 발행': 황금 점수 순이되,
        대표 사진이 아직 없는 글은 공개하지 않고 다음 순번 (사진은 공개 순번대로 먼저 채움)."""
        rows = [{"ID": 80, "post_name": "a", "post_title": "t", "post_date": "2026-11-05 10:00:00"},
                {"ID": 85, "post_name": "b", "post_title": "t", "post_date": "2026-11-05 11:00:00"}]
        self.assertEqual(self._publish_pick(rows, {80: 9.0, 85: 1.0}), 80)
        self.assertEqual(self._publish_pick(rows, {80: 9.0, 85: 1.0}, pending={80}), 85)

    def test_photos_follow_publish_order(self):
        """사진 채우기: 공개된 글 → 대기열 글을 공개 순서(황금 점수)대로 → 나머지 초안."""
        from bot import pending_images

        def wp(*args):
            if args[:2] == ("post", "list"):
                return "583\n" if "--post_status=publish" in args else "300\n301\n302\n"
            return ""
        with mock.patch.object(pending_images, "wp_cli", side_effect=wp), \
             mock.patch.object(pending_images.photo, "meta", side_effect=lambda i, k: "" if i == "300" else "ready"), \
             mock.patch.object(pending_images.schedule, "publish_order", return_value=[302, 301]) as order:
            self.assertEqual(pending_images.pending_posts(), ["583", "302", "301", "300"])
        self.assertEqual(sorted(order.call_args[0][0]), [301, 302])   # 공개 대기열 글만 줄 세움

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
        self.assertTrue(same("아르바이트이력서양식", "알바이력서양식"))   # 11/6 #885 뒤 다시 후보로 나옴
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

    def test_deploy_uses_main_head_not_default_branch(self):
        """예약 실행은 기본 브랜치에서 돌아 github.sha 가 main 보다 뒤처질 수 있음 → 검사·배포·기록 모두 gate 가 정한 main 버전 (11/5)."""
        wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        gate, rest = wf.split("\n  check:")
        check, deploy = rest.split("\n  deploy:")
        self.assertIn("/commits/main", gate)
        self.assertIn("github.token", gate)   # 비공개 저장소: 인증 없이 읽으면 실패
        self.assertIn("ref: ${{ needs.gate.outputs.sha }}", check)
        self.assertNotIn("github.sha", deploy)
        self.assertEqual(deploy.count("needs.gate.outputs.sha"), 4)  # 적용 단계 + 다시 시도 단계 (11/8 DeployRetryTest)
        self.assertIn("needs: [gate, check]", deploy)

    def test_shares_server_politely(self):
        """같은 서버(2코어)의 사주데이 앱: 11/6 10:06 배포 때 부하 5.14·/healthz 응답 없음 → 우리 일은 CPU·IO 몫을 낮게.
        cgroup v2 에선 Nice 가 서비스 사이에 안 먹어 CPUWeight·IOWeight(기본 100)로."""
        for name in ("bot", "discover", "backup"):
            unit = (ROOT / "server" / "systemd" / f"firstlab-{name}.service").read_text(encoding="utf-8")
            self.assertIn("CPUWeight=20", unit, name)
            self.assertIn("IOWeight=20", unit, name)
        wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        self.assertIn("systemd-run --scope --quiet -p CPUWeight=20 -p IOWeight=20", wf)
        for script in ("setup.sh", "wp-setup.sh", "bot-setup.sh", "bot-check.sh"):
            self.assertIn(f'$LOW bash "$REPO/server/{script}"', wf)

    def test_reviewed_list_change_deploys(self):
        """서버가 automation/reviewed.txt 를 읽음 (계산기 공개·검수 관문) → 이 파일만 바뀌어도 배포."""
        wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        paths = next(l for l in wf.splitlines() if l.strip().startswith("paths:"))
        self.assertIn('"automation/reviewed.txt"', paths)

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
        with mock.patch.object(freshness, "wp_cli", side_effect=wp), mock.patch.object(freshness, "problems", side_effect=lambda *a, **k: []):
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

    def test_alternative_in_other_article(self):
        """11/6 #960: 부동산등기규칙 제60조의2 '제60조에 따라 인감증명을 제출하여야 하는 자는 … 대신 … 본인서명사실확인서' —
        글이 '인감증명이 필요해요'만 세 곳에 씀. '제N조에 따라 … 대신' 조문도 특례로 확인하게."""
        from bot import condition_gate as g
        law = {"제60조": "제60조(인감증명의 제출) ① 다음 각 호의 인감증명을 제출하여야 한다.",
               "제60조의2": "제60조의2(본인서명사실확인서 또는 전자본인서명확인서 발급증의 제출) 제60조에 따라 인감증명을 제출하여야 하는 자는 "
                           "인감증명을 제출하는 대신 신청서 등에 서명을 하고 본인서명사실확인서를 제출할 수 있다.",
               "제61조": "제61조(인감증명 등의 유효기간) 제60조에 따른 인감증명은 발행일부터 3개월 이내의 것이어야 한다."}
        self.assertEqual(g.related_exceptions(law, "제60조"),
                         [("제60조의2", "본인서명사실확인서 또는 전자본인서명확인서 발급증의 제출")])   # 제61조('에 따른')는 아님

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
        alert = dw.check(state, t0 + timedelta(minutes=61), "aaa", "ccc")
        self.assertIn("61분째", alert)
        self.assertIn("검사가 모두 통과하면 GitHub 쪽", alert)   # 11/6 결제·한도로 막힘 — 글쓰기 작업이 고칠 것 없이 헤매지 않게
        self.assertIn("deploy_alert", state)
        self.assertIsNone(dw.check(state, t0 + timedelta(minutes=70), "ccc", "ccc"))      # 올라가면 풀림
        self.assertNotIn("deploy_alert", state)
        self.assertIsNone(dw.check(state, t0 + timedelta(minutes=80), "ccc", "ddd"))
        self.assertIsNone(dw.check(state, t0 + timedelta(minutes=125), "ddd", "eee"))     # 배포가 진행 중이면 알림 없음

    def test_reads_main_via_repo_origin_and_logs_failure(self):
        """11/6: 비공개 저장소를 주소만으로 ls-remote → 빈 값 → 75분 막혀도 알림 없음. 배포가 쓰는 origin 으로 읽고, 못 읽으면 기록."""
        from datetime import datetime
        from bot import deploy_watch as dw
        calls = []
        fake = mock.Mock(stdout="")
        with mock.patch.object(dw.subprocess, "run", side_effect=lambda a, **k: calls.append(a) or fake):
            self.assertEqual(dw.main_sha(), "")
        self.assertEqual(calls[0][:4], ["git", "-C", str(dw.REPO), "ls-remote"])
        self.assertIn("origin", calls[0])
        state, logs = {}, []
        with mock.patch.object(dw, "main_sha", return_value=""), \
                mock.patch.object(dw, "DEPLOYED") as dep, \
                mock.patch("bot.state.add_log", side_effect=lambda st, ok, msg: logs.append(msg)):
            dep.read_text.return_value = "aaa"
            dw.run(state, datetime(2026, 11, 6, 7, 0))
        self.assertEqual(logs, [dw.READ_FAIL])

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


class DeployTimeoutTest(unittest.TestCase):
    """배포 작업이 GitHub 쪽에서 '진행 중'으로 멈추면 다음 배포가 모두 대기 (11/6 05:43~) → 작업마다 시간 제한."""

    def test_each_job_has_timeout(self):
        text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("runs-on: ubuntu-latest"), text.count("timeout-minutes:"))
        self.assertIn("timeout-minutes: 70", text)


class DottedDateNumberTest(unittest.TestCase):
    """주장 속 날짜 '2027.1.1' 이 대조 숫자로 뽑혀 분수 표기 변환에서 float 오류 → 그 글 근거 대조 전체 실패 (11/6 #1023)."""

    def test_dotted_date_does_not_crash(self):
        from bot import source_check
        claim = "소득세법 제84조 2027.1.1 개정은 가상자산 호 신설로 번호만 바뀌고 기타소득금액 건별 5만 원 이하 비과세는 그대로"
        self.assertIn("2027.1.1", source_check.key_numbers(claim))
        self.assertTrue(source_check.has_number("부칙 2027.1.1 시행", "2027.1.1"))
        self.assertFalse(source_check.has_number("다른 글자", "2027.1.1"))
        review = [{"claim": claim, "verdict": "맞음", "action": "유지", "urls": ["https://www.law.go.kr/법령/소득세법/제84조"]}]
        got = source_check.check(review, fetcher=lambda u: claim + " " + "가" * 300)
        self.assertEqual(got["counts"][source_check.FOUND], 1)

    def test_missing_records_refilled_on_deploy(self):
        """기록이 안 남은 대기 글은 배포마다(bot.hold) 저장소 검수 기록으로 다시 대조 — 고친 코드로 스스로 채워짐."""
        from bot import hold
        done = []
        msgs = hold.missing_evidence([{"post_id": 1023, "keyword": "프리랜서3.3%계산기"}, {"post_id": 1019, "keyword": "x"},
                                      {"post_id": 9, "keyword": "검수 기록 없음"}], {1019},
                                     review_of=lambda pid, kw: [{"claim": "c"}] if pid != 9 else None,
                                     record=lambda pid, review: done.append(pid) or "숫자 주장 1개 중 확인 1")
        self.assertEqual(done, [1023])
        self.assertEqual(len(msgs), 1)

    def test_refill_not_in_deploy(self):
        """배포 '서버 적용'에서 공식 페이지를 열면 배포가 7분 넘게 걸림 (11/6) → 봇 10분 주기로."""
        self.assertNotIn("missing_evidence(", (ROOT / "bot" / "hold.py").read_text(encoding="utf-8").split("def main")[1])
        self.assertIn("hold.missing_evidence(*hold.ready_without_record(), limit=1)", (ROOT / "bot" / "run.py").read_text(encoding="utf-8"))


class ExcerptCaveatTest(unittest.TestCase):
    """요약이 본문 규칙만 옮기고 바로 뒤 '다만 …' 단서를 뺌 (11/6 #1026 해촉증명서 간이지급명세서)."""

    BODY = ("<p>거래처가 세무서에 <strong>간이지급명세서</strong>를 냈다면 해촉으로 소득이 줄어 조정을 신청할 때 증빙서류를 첨부한 것으로 봐서, "
            "따로 붙이지 않아도 돼요. 다만 그 명세서에 누락·오류가 있어 공단이 확인하지 못하면 사유를 증명하는 서류가 필요해요.</p>")

    def test_caveat_missing_in_excerpt(self):
        from bot.condition_gate import excerpt_caveats
        bad = "2025년 9월부터 간이지급명세서가 제출됐으면 해촉 사유 조정에 증빙을 따로 안 붙여도 돼요."
        self.assertEqual(len(excerpt_caveats(bad, self.BODY)), 1)
        self.assertEqual(excerpt_caveats(bad + " 다만 명세서에 누락·오류가 있어 공단이 확인하지 못하면 증명 서류가 필요해요.", self.BODY), [])
        self.assertEqual(excerpt_caveats("해촉증명서 양식의 칸을 정리했어요.", self.BODY), [])  # 다른 말이면 안 봄

    def test_wired(self):
        self.assertIn("excerpt_caveats", (ROOT / "bot" / "source_check.py").read_text(encoding="utf-8"))
        self.assertIn('"excerpt_caveats"', (ROOT / "automation" / "apply_review.py").read_text(encoding="utf-8"))


class LimitedEventYearTest(unittest.TestCase):
    """올해만 하는 무료·할인 행사를 연도 없이 써서 해마다 그런 것처럼 읽힘 (11/6 #1028 태권도원 모노레일 '연말까지 무료')."""

    def test_event_needs_year(self):
        from bot.condition_gate import limited_event_year
        self.assertEqual(len(limited_event_year("모노레일 4,000원(11·12월 주말 무료)", "<p>11월·12월 주말에는 모노레일도 무료로 탈 수 있어요.</p>")), 2)
        self.assertEqual(limited_event_year("모노레일 4,000원(2026년 11·12월 주말 무료)",
                                            "<p>올해(2026년)는 11월·12월 주말에 모노레일도 무료로 탈 수 있어요.</p>"), [])
        self.assertEqual(limited_event_year("", "<p>인천수목원은 입장 무료, 동절기 11월~2월 10:00~17:00이에요.</p>"), [])  # 해마다 같은 철 규칙
        self.assertIn('"limited_event_year"', (ROOT / "automation" / "apply_review.py").read_text(encoding="utf-8"))


class PhpNameClashTest(unittest.TestCase):
    """워드프레스가 한 번에 읽는 PHP(mu-plugins·테마)에 같은 이름의 함수·상수가 두 번 있으면 사이트 전체가 치명적 오류 (500).
    11/6 17:5x: 새 공식 사이트 링크 플러그인의 firstlab_official_links 가 구조화 데이터 플러그인의 같은 이름과 겹쳐 글·API 전부 500."""

    def test_no_duplicate_names(self):
        import collections
        files = list((ROOT / "wordpress" / "mu-plugins").glob("*.php")) + list((ROOT / "wordpress" / "themes").rglob("*.php"))
        funcs, consts = collections.Counter(), collections.Counter()
        for f in files:
            text = f.read_text(encoding="utf-8")
            funcs.update(set(re.findall(r"^\s*function\s+([A-Za-z_]\w*)\s*\(", text, re.M)))
            consts.update(set(re.findall(r"^\s*const\s+([A-Z_][A-Z0-9_]*)\s*=", text, re.M)))
        self.assertEqual([n for n, c in funcs.items() if c > 1], [])
        self.assertEqual([n for n, c in consts.items() if c > 1], [])


    def test_plugins_installed_before_wp_commands(self):
        """서버의 mu-plugin 이 치명적 오류면 wp 명령이 멈춤 → 고친 플러그인을 wp 명령보다 먼저 깔아야 배포가 스스로 복구됨 (11/6)."""
        lines = (ROOT / "server" / "wp-setup.sh").read_text(encoding="utf-8").splitlines()
        install = next(i for i, l in enumerate(lines) if "wordpress/mu-plugins/*.php" in l)
        first_wp = next(i for i, l in enumerate(lines) if re.match(r"\s*(\[.*\]\s*&&\s*)?wp\s", l) or "$(wp " in l)
        self.assertLess(install, first_wp)


class OfficialLinksTest(unittest.TestCase):
    """본문에 글자로만 적힌 공식 사이트를 화면에서 링크로 (11/6 운영자 '공식사이트 링크 만들어라' — 자동차세 납부 조회 글)."""

    def run_php(self, html):
        import os
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        code = ("<?php define('ABSPATH', 1); function add_filter() {} function esc_url($s) { return $s; }\n"
                f"require '{ROOT / 'wordpress' / 'mu-plugins' / 'first-lab-official-links.php'}';\n"
                "echo firstlab_official_autolink(getenv('H'));")
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(code)
        try:
            return subprocess.run(["php", f.name], capture_output=True, text=True, check=True, env={**os.environ, "H": html}).stdout
        finally:
            os.unlink(f.name)

    def test_domain_and_name(self):
        out = self.run_php("<p>서울은 서울시 ETAX(etax.seoul.go.kr), 그 밖은 위택스(wetax.go.kr)에서 조회해요. 위택스 앱도 있어요.</p>")
        self.assertIn('<a href="https://etax.seoul.go.kr"', out)
        self.assertIn('>wetax.go.kr</a>', out)
        self.assertEqual(out.count('class="firstlab-official"'), 2)     # 사이트마다 첫 곳만 (주소가 있으면 주소에)
        self.assertIn("위택스 앱도", out)

    def test_leaves_links_headings_and_lookalikes(self):
        html = ('<h2>정부24 신청</h2><p><a href="https://www.hometax.go.kr">홈택스</a>와 홈택스 앱, '
                'mygov.kr 과 정부24시간 상담</p>')
        out = self.run_php(html)
        self.assertIn("<h2>정부24 신청</h2>", out)              # 제목 안은 그대로
        self.assertEqual(out.count("hometax.go.kr"), 1)         # 이미 링크가 있는 사이트는 더 안 붙임
        self.assertNotIn('href="https://www.gov.kr"', out)      # 'mygov.kr'·'정부24시간'은 다른 말

    def test_bottom_section_deep_link_does_not_block_body(self):
        """11/9 운영자 'ITQ 글 왜 링크가 없노': 글 끝 '공식 안내 바로가기'의 하위 페이지 링크 때문에 본문 주소가 링크 안 됨."""
        out = self.run_php('<p>조회는 license.kpc.or.kr에서 해요.</p><h2>공식 안내 바로가기</h2>'
                           '<ul><li><a href="https://license.kpc.or.kr/nasec/refedu/refeduInfo.do">보수교육 안내</a></li></ul>')
        self.assertNotIn('>license.kpc.or.kr</a>', out)  # 목록 밖 주소는 봇 확인 기록이 있어야 함 — 아래에서 기록을 줌
        self.assertEqual(out.count("license.kpc.or.kr/nasec"), 1)
        out = self.run_php('<p>홈택스에서 신고해요.</p><h2>공식 안내 바로가기</h2>'
                           '<ul><li><a href="https://www.hometax.go.kr/websquare/a.do">홈택스 신고 안내</a></li></ul>')
        self.assertIn('class="firstlab-official">홈택스</a>에서', out)  # 글 끝 하위 링크가 있어도 본문은 링크

    def test_name_with_particle_linked(self):
        """11/6: '정부24에서'·'홈택스로'처럼 조사가 붙으면 링크가 안 됐음 ('정부24시간'은 계속 다른 말)."""
        out = self.run_php("<p>정부24에서 신청하거나 홈택스로는 내요. 숲나들e로 예약. 정부24시간 상담</p>")
        self.assertIn('class="firstlab-official">정부24</a>에서', out)
        self.assertIn('class="firstlab-official">홈택스</a>로는', out)
        self.assertIn('href="https://www.foresttrip.go.kr"', out)
        self.assertIn("정부24시간", out)

    def run_fill(self, html):
        import os
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        code = ("<?php define('ABSPATH', 1); function add_filter() {} function esc_url($s) { return $s; }\n"
                f"require '{ROOT / 'wordpress' / 'mu-plugins' / 'first-lab-official-links.php'}';\n"
                "echo firstlab_official_section_fill(firstlab_official_autolink(getenv('H')));")
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(code)
        try:
            return subprocess.run(["php", f.name], capture_output=True, text=True, check=True, env={**os.environ, "H": html}).stdout
        finally:
            os.unlink(f.name)

    def test_section_gets_named_site(self):
        """11/6 운영자: 사천 휴양림 글 '공식 안내 바로가기'에 본문이 안내하는 숲나들e 가 없었음 — 화면에서 끝 목록에 더함."""
        html = ('<p>숲나들e로 예약해요.</p><h2 class="wp-block-heading">공식 안내 바로가기</h2>'
                '<ul class="wp-block-list"><li><a href="https://www.law.go.kr/x">조례</a></li></ul>')
        out = self.run_fill(html)
        tail = out[out.index("공식 안내 바로가기"):]
        self.assertIn('href="https://www.foresttrip.go.kr"', tail)
        self.assertIn('class="firstlab-official">숲나들e</a>로', out)   # 본문 이름 링크도 그대로

    def test_section_not_duplicated_or_invented(self):
        listed = ('<p>홈택스에서 신청</p><h2>공식 안내 바로가기</h2><ul><li><a href="https://www.hometax.go.kr/x">홈택스</a></li></ul>')
        out = self.run_fill(listed)
        self.assertEqual(out[out.index("공식 안내 바로가기"):].count("hometax.go.kr"), 1)  # 목록에 이미 있으면 더 안 붙임
        self.assertIn('class="firstlab-official">홈택스</a>에서', out)  # 본문은 링크 (11/9: 글 끝 링크 때문에 본문이 안 되던 것)
        self.assertIn('href="https://www.gov.kr"', self.run_fill("<table><tr><td>등기소</td><td>정부24, 주민센터</td></tr></table>"))  # 표 칸 경계
        made = self.run_fill("<p>정부24에서 떼요.</p>")
        self.assertIn("공식 안내 바로가기</h2>", made)
        self.assertEqual(self.run_fill("<p>관계없는 글</p>"), "<p>관계없는 글</p>")

    def test_verified_hosts_from_bot(self):
        """목록 밖 공식 주소도 봇이 열어 본 기록이 있으면 링크 (11/6 운영자 '신뢰할 수 있는 공식 사이트면')."""
        import os
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        code = ("<?php define('ABSPATH', 1); function add_filter() {} function esc_url($s) { return $s; }\n"
                f"require '{ROOT / 'wordpress' / 'mu-plugins' / 'first-lab-official-links.php'}';\n"
                "echo firstlab_official_autolink('<p>소방안전원(kfsi.or.kr)과 이상한 xx.go.kr</p>', "
                "[['host' => 'kfsi.or.kr', 'url' => 'https://www.kfsi.or.kr'], ['host' => 'bad', 'url' => 'http://bad']]);")
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(code)
        try:
            out = subprocess.run(["php", f.name], capture_output=True, text=True, check=True, env=os.environ).stdout
        finally:
            os.unlink(f.name)
        self.assertIn('<a href="https://www.kfsi.or.kr"', out)
        self.assertNotIn('href="https://xx.go.kr"', out)     # 봇이 열어 보지 않은 주소는 링크 안 함
        self.assertNotIn("http://bad", out)

    def test_bot_records_only_reachable_official_hosts(self):
        from bot import official_links as o
        html = '<p>위택스(wetax.go.kr)·www.kfsi.or.kr·example.com·me@nps.or.kr·<a href="https://x.go.kr">hometax.go.kr</a> nope.go.kr</p>'
        self.assertEqual(o.hosts_in(html), ["wetax.go.kr", "kfsi.or.kr", "nope.go.kr"])
        ok = {"https://www.wetax.go.kr", "https://kfsi.or.kr"}
        self.assertEqual(o.verify(o.hosts_in(html), check=lambda u: u in ok),
                         [{"host": "wetax.go.kr", "url": "https://www.wetax.go.kr"}, {"host": "kfsi.or.kr", "url": "https://kfsi.or.kr"}])
        calls = []
        def wp(*a):
            calls.append(a)
            return json.dumps([{"id": 7, "content": html}]) if a[0] == "eval" else ""
        self.assertEqual(o.run(wp=wp, check=lambda u: u in ok, today="2026-11-06", exists=lambda u: False), "공식 사이트 링크 확인: 7(2)")
        saved = json.loads(calls[-1][-1])
        self.assertEqual((saved["hash"], len(saved["hosts"])), (o.content_hash(html), 2))
        self.assertEqual((saved["v"], saved["dead"]), (o.VERSION, ["nope.go.kr"]))  # 연결조차 안 되는 주소는 따로 (공개 전 보류)
        self.assertIn("official_links.run()", (ROOT / "bot" / "run.py").read_text(encoding="utf-8"))

    def test_max_links(self):
        out = self.run_php("<p>위택스 홈택스 정부24 복지로 고용24</p>")
        self.assertEqual(out.count('class="firstlab-official"'), 3)


class TocWebpTest(unittest.TestCase):
    """워드프레스 꿀팁 2·3번 (11/6 운영자 '2,3번만 해라'): 긴 글 목차, 화면용 사진 WebP."""

    def php(self, plugin, body):
        import os
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        stub = ("<?php define('ABSPATH', 1); function add_filter() {} function add_action() {}"
                " function wp_strip_all_tags($s) { return strip_tags($s); }"
                " function esc_attr($s) { return htmlspecialchars($s, ENT_QUOTES); } function esc_html($s) { return htmlspecialchars($s, ENT_QUOTES); }\n")
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(stub + f"require '{ROOT / 'wordpress' / 'mu-plugins' / plugin}';\n" + body)
        try:
            return subprocess.run(["php", f.name], capture_output=True, text=True, check=True, env=os.environ).stdout
        finally:
            os.unlink(f.name)

    def test_toc_for_long_posts_only(self):
        long = "<p>처음</p>" + "".join(f"<h2 class=\"wp-block-heading\">소제목 {i}</h2><p>내용</p>" for i in range(1, 5))
        out = self.php("first-lab-toc.php", f"echo firstlab_toc({json.dumps(long, ensure_ascii=False)});")
        self.assertEqual(out.count('id="toc-'), 4)
        self.assertLess(out.index("firstlab-toc"), out.index("<h2"))   # 첫 소제목 바로 위
        self.assertIn('<a href="#toc-3">소제목 3</a>', out)
        short = "<p>a</p><h2>하나</h2><h2>둘</h2>"
        self.assertEqual(self.php("first-lab-toc.php", f"echo firstlab_toc({json.dumps(short, ensure_ascii=False)});"), short)
        kept = self.php("first-lab-toc.php", "echo firstlab_toc('<h2 id=\"faq\">a</h2><h2>b</h2><h2>c</h2><h2>d</h2>');")
        self.assertIn('href="#faq"', kept)   # 이미 있는 주소는 그대로

    def test_srcset_prefers_webp(self):
        out = self.php("first-lab-webp.php", """echo json_encode(array_keys(firstlab_webp_only([
          1200 => ['url' => 'https://x/ai-1.jpg'], 1199 => ['url' => 'https://x/ai-1-1199x674.webp'], 768 => ['url' => 'https://x/ai-1-768x432.webp']])));
          echo "\n", json_encode(array_keys(firstlab_webp_only([1200 => ['url' => 'a.jpg'], 768 => ['url' => 'b.jpg']])));""").split()
        self.assertEqual(json.loads(out[0]), [1199, 768])
        self.assertEqual(json.loads(out[1]), [1200, 768])   # 아직 WebP 가 없으면 그대로 (사진이 안 사라지게)

    def test_backfill_summary(self):
        from bot import webp
        self.assertEqual(webp.summary({"done": [[1, "image/webp"], [2, "image/webp"]], "left": 40}), "사진 WebP 변환: 2/2장, 남은 40장")
        self.assertIn("WebP 아님: 3(image/jpeg)", webp.summary({"done": [[3, "image/jpeg"]], "left": 0}))
        self.assertIsNone(webp.summary({"done": [], "left": 0}))
        self.assertIn("webp.run()", (ROOT / "bot" / "run.py").read_text(encoding="utf-8"))


class SlotSplitTest(unittest.TestCase):
    """동시 글쓰기 후보 나누기는 키워드로 (11/6: 순번으로 나눴더니 목록이 갱신되며 순번이 밀려 두 칸이 같은 키워드를 씀)."""

    def test_same_keyword_same_slot_after_shift(self):
        import os
        import shutil
        import subprocess
        import zlib
        if not shutil.which("php"):
            self.skipTest("php 없음")
        src = (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8")
        func = src[src.index("function firstlab_api_slot_of"):src.index("function firstlab_api_held_drafts")]
        words = ["프리랜서3.3%계산기", "주휴수당뜻", "세후급여계산기", "전세자금대출계산기", "퇴직소득세계산기", "김해공항국제선주차장"]
        code = "<?php\n" + func + "foreach (json_decode(getenv('W'), true) as $w) echo firstlab_api_slot_of($w), \"\\n\";"
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(code)
        try:
            out = subprocess.run(["php", f.name], capture_output=True, text=True, check=True,
                                 env={**os.environ, "W": json.dumps(words + [" 프리랜서 3.3% 계산기 "])}).stdout.split()
        finally:
            os.unlink(f.name)
        slots = [int(x) for x in out]
        self.assertEqual(slots[:-1], [zlib.crc32(w.encode()) % 2 + 1 for w in words])  # 순서와 무관하게 키워드로만
        self.assertEqual(slots[-1], slots[0])  # 띄어쓰기만 다르면 같은 칸
        self.assertEqual(set(slots), {1, 2})
        self.assertNotIn("$i % 2", src)  # 순번 나누기로 되돌리지 않게


class PrepublishReviewTest(unittest.TestCase):
    """공개 직전 재검수 (11/6 운영자 '공개 전에 한 번 더 — 그렇게 만들어라'): 공개 순서상 다음 글을 글쓰기 작업이 다시 검수,
    공개는 재검수 마친 글 먼저, 없으면 예전처럼 (공개가 멈추지 않게), 며칠 지나면 다시 (평생 한 번이 아니게)."""

    def test_pick_next_in_publish_order(self):
        from bot import review_queue as rq
        order, titles = [30, 10, 20], {30: "가", 10: "나", 20: "다"}
        got = rq.prepublish_pick(order, {}, titles, "2026-11-06", ahead=2)
        self.assertEqual((got["post_id"], got["kind"]), (30, "prepublish"))
        got = rq.prepublish_pick(order, {30: "2026-11-06"}, titles, "2026-11-06", ahead=2)
        self.assertEqual(got["post_id"], 10)
        self.assertIsNone(rq.prepublish_pick(order, {30: "2026-11-06", 10: "2026-11-05"}, titles, "2026-11-06", ahead=2))
        # 하루 공개 수(ahead) 밖의 글은 아직 안 함
        self.assertIsNone(rq.prepublish_pick(order, {30: "2026-11-06"}, titles, "2026-11-06", ahead=1))

    def test_review_expires(self):
        from bot import review_queue as rq
        self.assertTrue(rq.fresh("2026-11-03", "2026-11-06"))
        self.assertFalse(rq.fresh("2026-11-02", "2026-11-06"))
        self.assertFalse(rq.fresh("", "2026-11-06"))
        got = rq.prepublish_pick([30], {30: "2026-10-20"}, {}, "2026-11-06", ahead=1)
        self.assertEqual(got["post_id"], 30)  # 오래전 재검수 → 다시

    def test_publish_prefers_reviewed_but_never_stalls(self):
        from bot import review_queue as rq
        self.assertEqual(rq.prefer_prepublished([30, 10, 20], {20: "2026-11-06"}, "2026-11-06"), [20, 30, 10])
        self.assertEqual(rq.prefer_prepublished([30, 10, 20], {}, "2026-11-06"), [30, 10, 20])  # 재검수 없으면 그대로 공개

    def test_dates_read_in_one_call(self):
        from bot import review_queue as rq
        calls = []
        got = rq.prepublish_dates([5, 6], wp=lambda *a: calls.append(a) or '{"5": "2026-11-06", "6": ""}')
        self.assertEqual(got, {5: "2026-11-06", 6: ""})
        self.assertEqual(len(calls), 1)
        self.assertEqual(rq.prepublish_dates([5], wp=lambda *a: (_ for _ in ()).throw(RuntimeError())), {})

    def test_done_file_marks_post(self):
        from unittest import mock
        from bot import publish_queue
        calls = []
        with mock.patch.object(publish_queue, "wp_cli", lambda *a: calls.append(a) or ""), \
                mock.patch.object(publish_queue.freshness, "mark_verified", lambda pid, when=None: calls.append(("verified", pid, str(when)))):
            publish_queue.mark_prepublished(2015, "reviewdone-2015-20261106-1430.json")
            publish_queue.mark_prepublished(7, "reviewdone-7-2026-11-07-0910.json")
        self.assertIn(("post", "meta", "update", "2015", "_firstlab_prepublish_at", "2026-11-06"), calls)
        self.assertIn(("verified", 2015, "2026-11-06"), calls)
        self.assertIn(("post", "meta", "update", "7", "_firstlab_prepublish_at", "2026-11-07"), calls)

    def test_wired_in(self):
        sched = (ROOT / "bot" / "schedule.py").read_text(encoding="utf-8")
        self.assertIn("prefer_prepublished", sched)
        self.assertIn("mark_prepublished", (ROOT / "bot" / "publish_queue.py").read_text(encoding="utf-8"))
        self.assertIn("target(ahead=", (ROOT / "bot" / "run.py").read_text(encoding="utf-8"))
        self.assertIn('"prepublish": true', (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))


class PlanCountTest(unittest.TestCase):
    """예약 공개 시각 줄 수 = 오늘 공개 계획 수 = 하루 공개 수 (11/6 운영자 '3개 넣으면 3개, 4개 넣으면 4개')."""

    def test_any_number_of_lines(self):
        from bot import schedule
        for n in range(1, 9):
            times = [f"{8 + i:02d}:00" for i in range(n)]
            for day in ("2026-11-06", "2026-12-25", "2027-03-01"):
                self.assertEqual(len(schedule.plan_times(times, 100, day)), n)

    def test_same_minute_kept_apart(self):
        """랜덤으로 같은 분이 되면 '했음' 기록이 합쳐져 한 편이 빠짐 → 겹치지 않게."""
        import datetime as dt
        from bot import schedule
        for i in range(730):
            day = (dt.date(2026, 11, 6) + dt.timedelta(i)).isoformat()
            for times in (["09:00", "10:00"], ["00:00", "00:01", "00:02"], ["23:59", "23:58", "23:30", "23:00"]):
                plan = schedule.plan_times(times, 100, day)
                self.assertEqual(len(set(plan)), len(times), (day, times, plan))


class ScheduleSavedTest(unittest.TestCase):
    """설정 저장 직후 화면이 옛 계획을 보여 '반영 안 됨'으로 보임 (11/6 운영자) → 저장한 시각과 계획을 만든 시각이 다르면 알림.
    설정 페이지(PHP)가 시각을 읽는 규칙이 봇(bot/settings.py)과 같아야 알림이 맞음."""

    SAMPLES = ["09:00\n18:00", "9:00 20:30", "9시 30분\n18시", "09:00\n09:00", "25:00\n7:5", "", "08:00, 12:00, 15:00, 21:00"]

    def test_php_reads_times_like_bot(self):
        import os
        import shutil
        import subprocess
        from bot.settings import _times
        if not shutil.which("php"):
            self.skipTest("php 없음")
        plugin = ROOT / "wordpress" / "mu-plugins" / "first-lab-bot-settings.php"
        src = plugin.read_text(encoding="utf-8")
        func = src[src.index("function firstlab_bot_schedule_times"):src.index("function firstlab_bot_get")]
        code = "<?php\n" + func + "foreach (json_decode(getenv('S'), true) as $t) echo json_encode(firstlab_bot_schedule_times($t)), \"\\n\";"
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(code)
        try:
            out = subprocess.run(["php", f.name], capture_output=True, text=True, check=True,
                                 env={**os.environ, "S": json.dumps(self.SAMPLES)}).stdout.splitlines()
        finally:
            os.unlink(f.name)
        self.assertEqual([json.loads(x) for x in out], [_times(t) for t in self.SAMPLES])

    def test_bot_records_plan_source(self):
        self.assertIn('state["plan_from"]', (ROOT / "bot" / "run.py").read_text(encoding="utf-8"))
        self.assertIn("plan_from", (ROOT / "wordpress" / "mu-plugins" / "first-lab-bot-settings.php").read_text(encoding="utf-8"))


class PublishPlanVisibleTest(unittest.TestCase):
    """운영자가 바꾼 '예약 공개 시각'이 오늘 계획에 들어갔는지 감시가 볼 수 있게 (11/6 '09:00·18:00 두 줄로 바꿨다')."""

    def test_tick_shows_plan(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import tick
        line = tick.plan_line({"2026-11-06": ["08:31", "18:12"]}, {"2026-11-06": ["08:31"]})
        self.assertEqual(line, "08:31 ✅ · 18:12 ⏳")
        self.assertIn("없음", tick.plan_line({}, {}))

    def test_maintenance_exposes_plan(self):
        php = (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8")
        self.assertIn("'publish_plan'", php)


class TickDeployTest(unittest.TestCase):
    def test_only_latest_finished_deploy_counts(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import tick
        run = lambda sha, st, c, name="Deploy server code": {"name": name, "status": st, "conclusion": c, "head_sha": sha * 7, "id": 1}  # noqa: E731
        self.assertIsNone(tick.deploy_problem([run("b", "completed", "success"), run("a", "completed", "failure")]))   # 뒤 배포가 성공
        self.assertIn("aaaaaaa", tick.deploy_problem([run("c", "in_progress", None), run("a", "completed", "failure")]))
        self.assertIsNone(tick.deploy_problem([run("h", "completed", "failure", "Health check")]))                     # 배포가 아닌 작업

    def test_billing_block_is_named(self):
        """11/5: 비공개 저장소 Actions 무료 분 소진 → 작업이 러너 없이 실패, 로그 404. 표시(annotation)로 원인을 바로 보임."""
        sys.path.insert(0, str(ROOT / "automation"))
        import tick
        msg = "The job was not started because recent account payments have failed or your spending limit needs to be increased."
        self.assertTrue(tick.billing_blocked([{"message": "ubuntu-latest label will migrate"}, {"message": msg}]))
        self.assertFalse(tick.billing_blocked([{"message": "Process completed with exit code 1."}]))
        pages = {"/jobs": {"jobs": [{"id": 9, "name": "gate", "conclusion": "failure"}]}, "/annotations": [{"message": msg}]}
        with mock.patch.object(tick, "get", side_effect=lambda u: next(v for k, v in pages.items() if u.endswith(k))):
            self.assertIn("결제", tick.failure_reason({"id": 1}))


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


class ExceptionAckTest(unittest.TestCase):
    def test_ack_items_do_not_cascade(self):
        """'제N조 해당 없음' 확인 항목의 조문 특례를 또 요구하지 않음 (11/5 일용직 4대보험 글: 근거 대조 9분씩 되풀이)."""
        from bot import condition_gate as g
        arts = {"제48조의3": "제48조의3(특례) 내용", "제48조의4": "제48조의4(플랫폼 특례) 제48조의3에도 불구하고 따로 정한다."}
        url = "https://www.law.go.kr/법령/보험료징수법/제48조의3"
        ack = [{"claim": "제48조의3 해당 없음 — 노무제공자 특례 (보험료징수법 제48조의3)", "verdict": "맞음", "action": "유지", "urls": [url]}]
        self.assertEqual(g.exception_articles(ack, lambda base: arts), [])
        real = [dict(ack[0], claim="노무제공자 보험료는 따로 정해요 (보험료징수법 제48조의3)")]
        self.assertTrue(g.exception_articles(real, lambda base: arts))

    def test_law_cache_same_day(self):
        import tempfile
        from bot import condition_gate as g
        calls = []
        with mock.patch.object(g, "LAW_CACHE_DIR", Path(tempfile.mkdtemp())):
            make = lambda: calls.append(1) or {"제1조": "x"}  # noqa: E731
            self.assertEqual(g._cached("articles", "u", make), {"제1조": "x"})
            self.assertEqual(g._cached("articles", "u", make), {"제1조": "x"})
            self.assertEqual(len(calls), 1)                                   # 두 번째는 저장본
            self.assertEqual(g._cached("articles", "v", lambda: {}), {})      # 못 받은 것은 저장 안 함


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

    def test_unknown_law_names(self):
        """11/9 #2362: '어선원 재해보상보험법' — 정식은 '어선원 및 어선 재해보상보험법'. 약칭(기간제법)·흔한 말(개정법)은 통과."""
        from bot import condition_gate as g
        names = {"선원법", "어선원및어선재해보상보험법", "사립학교교직원연금법", "기간제법", "근로기준법"}
        known = lambda n: n.replace(" ", "") in names
        body = "<p>선원법·어선원 재해보상보험법·사립학교교직원 연금법으로 보상, 기간제법에 따라, 근로기준법 제2조, 개정법, 계산 방법</p>"
        self.assertEqual([x.split("'")[1] for x in g.unknown_law_names(body, known)], ["재해보상보험법"])
        self.assertEqual(g.unknown_law_names(body.replace("어선원 재해", "어선원 및 어선 재해"), known), [])
        self.assertEqual(g.unknown_law_names(body, lambda n: None), [])  # 법제처를 못 열면 막지 않음

    def test_excerpt_principle(self):
        """11/9 #2383: 요약 '신고기한부터 9개월 안에 결정' ↔ 본문 '원칙적으로 … 9개월 안에, 부득이하면 더 걸림'."""
        from bot import condition_gate as g
        body = "<p>이 결정은 원칙적으로 신고기한부터 9개월 안에 해요. 부득이한 사유가 있으면 더 걸릴 수 있어요.</p>"
        self.assertTrue(g.excerpt_principle("신고기한부터 9개월 안에 결정해요.", body))
        self.assertEqual(g.excerpt_principle("원칙적으로 신고기한부터 9개월 안에 결정해요.", body), [])
        self.assertEqual(g.excerpt_principle("청구 기간(9개월·3개월)", body), [])  # 예외 기간도 같이 적으면 됨

    def test_excerpt_number_limits(self):
        """11/9 #2362: 요약 '미신고 때 급여 50% 징수' ↔ 본문 '보험급여의 50%(… 산재보험료의 5배까지)'."""
        from bot import condition_gate as g
        body = "<p>신고를 미룬 기간에 사고가 나면 지급된 보험급여의 50%(그동안 냈어야 할 산재보험료의 5배까지)를 물어내요.</p><p>세금은 22%(납부고지 전까지)예요.</p>"
        self.assertTrue(g.excerpt_number_limits("미신고 때 급여 50% 징수", body))
        self.assertEqual(g.excerpt_number_limits("미신고 때 급여 50%(보험료 5배 한도) 징수", body), [])
        self.assertEqual(g.excerpt_number_limits("세율 22%", body), [])  # 숫자 없는 시점 괄호는 한도 아님

    def test_title_tier_amount(self):
        """11/9 #2295: 제목 '900만 원 채우면 148만 5,000원'은 16.5% 구간 값 — 본문 13.2% 구간은 118만 8,000원."""
        from bot import condition_gate as g
        body = "<p>총급여 5,500만 원 이하는 16.5%, 넘으면 13.2%예요.</p><p>900만 원을 넣으면 148만 5,000원, 13.2%면 118만 8,000원이에요.</p>"
        self.assertTrue(g.title_tier_amount("퇴직연금 세액공제: 900만 원 채우면 148만 5,000원", "", body))
        self.assertEqual(g.title_tier_amount("퇴직연금 세액공제: 900만 원 채우면 최대 148만 5,000원", "", body), [])
        self.assertEqual(g.title_tier_amount("퇴직연금 세액공제: 900만 원 채우면 148만 5,000원", "", "<p>16.5% 공제라 148만 5,000원이에요.</p>"), [])  # 구간이 하나면 그대로


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
        with mock.patch.object(freshness, "wp_cli", side_effect=wp), mock.patch.object(freshness, "problems", side_effect=lambda *a, **k: []), \
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


class AttachmentTextTest(unittest.TestCase):
    """근거 대조가 공공기관 게시글의 첨부(hwpx·PDF) 글자도 읽음 (11/5 #808·#842 첨부에만 있는 금액이 '페이지에 없음')."""

    def test_hwpx_and_links(self):
        import io
        import zipfile
        from bot import source_check as sc
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Contents/section0.xml", "<hp:t>상한액 336,840원</hp:t>")
        hwpx = buf.getvalue()
        self.assertIn("336840", sc.doc_text(hwpx))
        page = '<a href="/common/fileDown.do?id=1">첨부</a><a href="/other.html">x</a>'
        seen = []
        raw_of = lambda u: (seen.append(u), (hwpx, None, ""))[1]  # noqa: E731
        self.assertIn("336840", sc.attachment_text(page, "https://www.moel.go.kr/news/view.do?x=1", raw_of))
        self.assertEqual(seen, ["https://www.moel.go.kr/common/fileDown.do?id=1"])
        self.assertEqual(sc.attachment_text(page, "https://blog.example.com/a", raw_of), "")   # 공공기관만
        self.assertEqual(sc.doc_text(b"not a doc"), "")

    def test_percent_claims_are_checked(self):
        """비율은 두 자리여도 대조 (11/5 #861 '3자녀 20%·2자녀 10%'가 숫자 주장 0개)."""
        from bot import source_check as sc
        self.assertEqual(sc.key_numbers("자녀 3명 이상이면 통행료의 20% (유료도로법 시행령 제8조)"), ["20"])
        self.assertTrue(sc.has_number("통행료의 100분의 20을 감면", "20"))
        self.assertEqual(sc.key_numbers("구간 예시: 5,000만 원 × 2.5% × 30/365 = 102,739원"), [])   # 계산식 가정 비율은 빼기

    def test_man_unit_numbers(self):
        from bot import source_check as sc
        self.assertTrue(sc.has_number("생계급여 87 만 5533 원", "875533"))
        self.assertTrue(sc.has_number("215만 710원", "2150710"))
        self.assertTrue(sc.has_number("300만 원", "3000000"))
        self.assertFalse(sc.has_number("87만 5534원", "875533"))


class CheckAndPushTest(unittest.TestCase):
    def test_script_stops_on_failure(self):
        """감시 세션의 올리기 명령은 테스트 실패 시 멈춤 (11/5 '| tail -1 &&' 로 실패한 채 올라감)."""
        sh = (ROOT / "automation" / "check_and_push.sh").read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", sh)
        self.assertIn("|| { tail -30", sh)
        code = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("| tail -1 &&", code)

    def test_writer_retries_github_side_push_errors(self):
        """11/6 08:25 글쓰기 실행 94번: GitHub 'commit_refs_rules' 일시 거부(약 5분)에 바로 멈춰 다 쓴 글이 안 올라감 → 몇 분에 걸쳐 다시."""
        doc = (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8")
        self.assertIn("30초·1분·2분·4분 쉬고 다시", doc)
        self.assertIn("fatal error", doc)

    def test_push_failures_are_not_hidden(self):
        """11/6: 네 번 실패한 main push 를 성공처럼 끝냄 · 브랜치 '-f' push 가 권한에 막히고 '|| true' 가 숨김."""
        sh = (ROOT / "automation" / "check_and_push.sh").read_text(encoding="utf-8")
        code = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
        self.assertIn('[ -n "$pushed" ] ||', code)
        self.assertNotIn("push -q -f", code)
        self.assertNotIn("|| true", code)


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


class ExcerptApplyDateTest(unittest.TestCase):
    def test_flag_and_pass(self):
        """#817: 요약 '11월 27일부터 가산금 10%에서 40%' ↔ 본문 '시행 이후 거짓 신고한 경우부터 적용'."""
        from bot import condition_gate as g
        body = "<p>개정 규정은 시행 이후 거짓으로 신고한 경우부터 적용해요. 그 전 신고분은 종전 10%예요.</p>"
        self.assertTrue(g.excerpt_apply_date("2026년 11월 27일부터 가산금이 10%에서 40%로 올라요.", body))
        self.assertEqual(g.excerpt_apply_date("2026년 11월 27일 이후 거짓 신고하면 가산금이 10%에서 40%로 올라요.", body), [])
        self.assertEqual(g.excerpt_apply_date("2026년 11월 27일부터 가산금이 10%에서 40%로 올라요.", "<p>올라요.</p>"), [])

    def test_existing_value_mixed_into_start_date(self):
        """#857: 요약 '2027년 2월 25일부터 벌점 10점, 범칙금 6만 원' ↔ 본문 '지금도 범칙금 6만 원'."""
        from bot import condition_gate as g
        body = "<p>지금도 어기면 범칙금(승용차 6만 원)이 나오는데, 2027년 2월 25일부터는 벌점 10점도 붙어요.</p>"
        self.assertTrue(g.excerpt_apply_date("2027년 2월 25일부터 벌점 10점, 승용차 범칙금은 6만 원이에요.", body))
        self.assertEqual(g.excerpt_apply_date("지금도 범칙금 6만 원인데, 2027년 2월 25일부터는 벌점 10점도 붙어요.", body), [])

    def test_transition_in_body(self):
        """#832: 요약 '2026년 1월 1일부터 생일 전후 6개월' ↔ 본문 '2026년 전 발급자는 예전 기간 중 하나'."""
        from bot import condition_gate as g
        body = "<p>2026년 전에 면허증을 받은 사람은 첫 갱신 때 새 기간과 예전 기간 중 하나를 골라요.</p>"
        self.assertTrue(g.excerpt_apply_date("갱신기간은 2026년 1월 1일부터 생일 전후 6개월이에요.", body))
        self.assertEqual(g.excerpt_apply_date("갱신기간은 2026년 1월 1일부터 생일 전후 6개월이에요. 2026년 전에 받은 사람은 첫 갱신 때 예전 기간도 돼요.", body), [])


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


class BokNamedTest(unittest.TestCase):
    """예금·적금 금리, 원/달러 환율, 소비자물가 (11/6 운영자 'API 다 써서' 4번) — 항목 코드는 서버가 이름으로 찾음."""

    def test_named_values_found_by_name_with_cpi_yoy(self):
        import datetime as dt
        from bot import bok_api
        listing = {"121Y002": [("BEABAA21", "정기예금", "M"), ("BEABAA22", "정기적금", "M")],
                   "731Y001": [("0000001", "원/미국달러(매매기준율)", "D")], "901Y009": [("0", "총지수", "M")]}
        data = {"BEABAA21": [("202609", 2.8)], "BEABAA22": [("202609", 3.1)], "0000001": [("20261105", 1385.5)],
                "0": [("202510", 116.0), ("202610", 118.4)]}
        got, missing = bok_api.named_values(dt.date(2026, 11, 6), fetch_series=lambda stat, code, *a: data[code],
                                            listing=lambda stat: listing[stat])
        self.assertEqual(missing, [])
        self.assertEqual(got["deposit"]["value"], 2.8)
        self.assertEqual(got["cpi"]["yoy"], 2.1)
        self.assertEqual(bok_api.describe(got["usd"]), "원/달러 환율 1385.5(20261105)")
        self.assertEqual(bok_api.describe(got["cpi"]), "소비자물가지수 118.4(202610, 전년 대비 2.1%)")
        got, missing = bok_api.named_values(dt.date(2026, 11, 6), fetch_series=lambda *a: [], listing=lambda stat: [])
        self.assertEqual((got, len(missing)), ({}, 4))                       # 못 찾으면 빠지고 기록에 '못 찾음'


class DeployCheckReportTest(unittest.TestCase):
    def test_deploy_check_shows_search_console_and_new_rates(self):
        """배포 점검 기록(/maintenance)에 서치콘솔 연결 여부·한도 안 넘는 줄 수 (11/6 'API 다 써서' 5번: 연결 여부가 안 보였음)."""
        sh = (ROOT / "server" / "bot-check.sh").read_text(encoding="utf-8")
        self.assertIn("서치콘솔: 미연동", sh)
        self.assertIn("tail -5", sh)                       # 법령·혜택·네이버·서치콘솔 네 줄 + 여유
        self.assertIn("named_values", (ROOT / "bot" / "bok_api.py").read_text(encoding="utf-8"))


class TitleSeoTest(unittest.TestCase):
    """제목 SEO (11/6 운영자 1~5번): 길이 관문·경고, 작가 규칙 형식, 글 제목에서 사이트 이름 빼기, 검사는 서버의 지금 본문으로."""

    def test_title_length_gate(self):
        from bot import condition_gate, review_gate
        long = "기초연금 신청: 2027년 1월 21일 이후 65세가 되는 장애인연금 수급권자는 신청한 것으로 봐요"   # #847 55자
        self.assertEqual(len(condition_gate.title_length(long)), 1)
        self.assertEqual(condition_gate.title_length("기초연금 신청 방법: 자격·금액과 2027년 바뀌는 점"), [])
        self.assertIn("title_length(title)", (ROOT / "bot" / "review_gate.py").read_text(encoding="utf-8"))
        self.assertTrue(any("제목 55자" in x for x in review_gate.problems([{"claim": "2027년 1월 21일 65세", "urls": [], "verdict": "맞음", "action": "유지"}], long, "", "")))

    def test_title_numbers_warning(self):
        from bot.condition_gate import title_numbers
        self.assertEqual(title_numbers("건설기계조종사 안전교육: 3년마다 4시간, 교육비 32,000원"), ["3", "4", "32,000"])   # #973
        self.assertEqual(title_numbers("한전 고효율 가전 환급 2026: 복지할인 가구 최대 30만 원"), ["30"])               # 연도는 셈 안 함
        self.assertIn("[제목 숫자]", (ROOT / "bot" / "source_check.py").read_text(encoding="utf-8"))

    def test_writer_rule_and_branding(self):
        self.assertIn("35자 안팎·최대 45자", (ROOT / "bot" / "writer.py").read_text(encoding="utf-8"))
        self.assertIn("the_seo_framework_use_title_branding", (ROOT / "wordpress" / "mu-plugins" / "first-lab-site.php").read_text(encoding="utf-8"))
        self.assertIn("TITLE_WARN", (ROOT / "bot" / "source_check.py").read_text(encoding="utf-8"))

    def test_apply_review_uses_current_body(self):
        import tempfile
        sys.path.insert(0, str(ROOT / "automation"))
        import apply_review
        d = Path(tempfile.mkdtemp()) / "post-1.json"
        d.write_text(json.dumps({"title": "지금 제목"}), encoding="utf-8")
        self.assertEqual(apply_review.current_post(1, fetch=lambda pid: d)["title"], "지금 제목")


class LogNoiseTest(unittest.TestCase):
    """11/6: 배포마다 같은 '황금 키워드 집계'·'문제 0개'가 쌓여 30줄 기록에서 새벽 수집 기록이 밀려남 → 하루 한 번만, 기록 100줄."""

    def test_same_message_once_a_day(self):
        from bot import state as st
        s = {"log": []}
        self.assertTrue(st.add_log_once_a_day(s, True, "황금 키워드 집계: 같음"))
        self.assertFalse(st.add_log_once_a_day(s, True, "황금 키워드 집계: 같음"))
        self.assertTrue(st.add_log_once_a_day(s, True, "황금 키워드 집계: 바뀜"))
        self.assertEqual(len(s["log"]), 2)
        self.assertGreaterEqual(st.MAX_LOG, 100)


class TempStatusTest(unittest.TestCase):
    """11/6 #1012 '지금은 구제역 휴원 중' — 다시 열면 글이 틀려짐 → 근거 공식 페이지에서 그 상태 말이 사라지면 공개 전·공개 뒤 다시 확인."""

    def test_flag_when_status_word_gone(self):
        import datetime as dt
        from bot import freshness
        record = {"found": [{"claim": "31종", "numbers": [], "urls": ["https://www.incheon.go.kr/park/park010214"]}]}
        closed = lambda url: "※구제역 발생 방지를 위해 휴원중(2026년 9월 21일 ~ 구제역 발생 종료 시) 31종"   # noqa: E731
        reopened = lambda url: "관람시간 10:00~17:00 31종"                                          # noqa: E731
        head = "인천대공원 동물원: 무료 입장·31종, 지금은 구제역 휴원 중"
        d = dt.date(2026, 11, 6)
        self.assertEqual(freshness.problems(record, [], d, d, closed, headline=head), [])
        got = freshness.problems(record, [], d, d, reopened, headline=head)
        self.assertTrue(any("'휴원'" in x for x in got))
        self.assertEqual(freshness.problems(record, [], d, d, reopened, headline="인천대공원 동물원 무료 입장"), [])   # 상태 말 없는 제목
        self.assertEqual(freshness.problems(record, [], d, d, lambda u: None, headline=head), [])                     # 못 열면 판단 안 함


class ExcerptQualifierTest(unittest.TestCase):
    """11/6 #1006: 본문 '금~일요일과 법정공휴일 15,000원' ↔ 요약 '금~일 15,000원' (평일 공휴일도 13,000원으로 읽힘)."""

    def test_partial_condition_in_excerpt(self):
        from bot.condition_gate import excerpt_qualifiers
        body = "<p>하루 최대 월~목 13,000원, 금~일요일과 법정공휴일 15,000원이에요.</p>"
        self.assertEqual(len(excerpt_qualifiers("하루 최대 월~목 13,000원·금~일 15,000원이에요.", body)), 1)
        self.assertEqual(excerpt_qualifiers("하루 최대 월~목 13,000원·금~일·공휴일 15,000원이에요.", body), [])
        self.assertEqual(excerpt_qualifiers("24시간 최대 P1·P2 10,000원(금~일·공휴일 15,000원)", "<p>금~일·법정공휴일 10,000원</p>"), [])  # 조건 없이 쓴 금액은 안 봄 (#655)
        self.assertEqual(excerpt_qualifiers("평일 25,000원", "<p>비수기 일·월 2박 25,000원 × 2 = 50,000원</p>"), [])         # 계산 예시 (#998)


class CrossLawTest(unittest.TestCase):
    """다른 법률의 예외 (11/6 #989 노동절법·#996 가사근로자법 — 같은 꼴 2번 → 알려진 예외 목록으로 경고)."""

    def test_known_cross_law_exceptions(self):
        from bot.condition_gate import cross_law_exceptions
        b989 = "<p>상시 4명 이하 사업장에는 이 조항이 적용되지 않아, 공휴일은 근로계약에서 정한 경우에만 쉬어요.</p>"
        b996 = "<li>동거하는 친족만 쓰는 사업장·가사 사용인: 근로기준법 자체가 적용되지 않아요.</li>"
        self.assertEqual(len(cross_law_exceptions(b989)), 1)
        self.assertEqual(len(cross_law_exceptions(b996)), 1)
        self.assertEqual(cross_law_exceptions(b989 + "<p>단, 노동절(5월 1일)은 규모와 상관없이 유급휴일이에요.</p>"), [])
        self.assertEqual(cross_law_exceptions(b996 + "<p>가사근로자는 근로기준법이 적용돼요.</p>"), [])
        self.assertEqual(cross_law_exceptions("<p>연장근로 가산은 50%예요.</p>"), [])
        self.assertIn('"cross_law_exceptions"', (ROOT / "automation" / "apply_review.py").read_text(encoding="utf-8"))


class CalcCoverageTest(unittest.TestCase):
    """11/6 #989 근무일수 계산기: 공휴일 목록이 2026~2027년뿐인데 글은 '대체공휴일까지 반영해 바로 세어 줘요' → 본문에 기간을 밝혀야 통과."""

    def test_holiday_range_must_be_stated(self):
        import tempfile
        from bot.condition_gate import calc_coverage
        d = Path(tempfile.mkdtemp())
        (d / "work-days.json").write_text(json.dumps({"holiday_from": "2026-01-01", "holiday_to": "2027-12-31"}), encoding="utf-8")
        body = '<p>2026·2027년 월별 근무일수</p><p>[firstlab_calc id="work-days"]</p><p>계산기는 관공서 공휴일만 빼요.</p>'
        self.assertEqual(len(calc_coverage(body, d)), 1)                      # 표 제목 '2026·2027년'은 기간 안내가 아님
        fixed = body.replace("관공서 공휴일만", "2026~2027년 관공서 공휴일·대체공휴일만")
        self.assertEqual(calc_coverage(fixed, d), [])
        self.assertEqual(calc_coverage('<p>[firstlab_calc id="vat"]</p>', d), [])   # 기간 없는 계산기는 상관없음
        self.assertIn("calc_coverage(body_html)", (ROOT / "bot" / "review_gate.py").read_text(encoding="utf-8"))


class RegateTest(unittest.TestCase):
    """11/6 #980: 검사 쪽 오탐(공식 주소 목록 누락)으로 보류·포기된 글 — 배포 때 지금 코드로 다시 검사해 통과하면 바로 공개 대기열로."""

    def test_released_when_current_code_passes(self):
        import tempfile
        from bot import hold
        d = Path(tempfile.mkdtemp())
        (d / "hanjeon.json").write_text(json.dumps({"keyword": "한전고효율가전환급", "review": [{"claim": "a"}]}), encoding="utf-8")
        (d / "fixhold-980-20261106-1129.json").write_text(json.dumps({"type": "fix_hold", "post_id": 980, "give_up": True}), encoding="utf-8")
        self.assertEqual(hold.latest_review(980, "한전고효율가전환급", d), [{"claim": "a"}])   # 포기 파일엔 review 없음 → 처음 글 파일
        released = []
        held = [{"post_id": 980, "keyword": "한전고효율가전환급", "title": "t"}, {"post_id": 981, "keyword": "x", "title": "u"}]
        msgs = hold.regate(held, review_of=lambda pid, kw: [{"claim": "a"}] if pid in (980, 981) else None,
                           problems=lambda review, t, e, c: [] if t == "t" else ["아직 막힘"],
                           release=lambda pid, review: released.append(pid))
        self.assertEqual(released, [980])
        self.assertEqual(len(msgs), 1)
        self.assertIn("bot.hold", (ROOT / "server" / "bot-setup.sh").read_text(encoding="utf-8"))


class LeadExceptionTest(unittest.TestCase):
    """첫 문단이 규칙을 그냥 말하고 예외는 아래에만 (11/5 #821·#828, 11/6 #928 — 같은 꼴 3번 → 검사, 글쓰기 사전 점검 경고)."""

    def test_flags_rule_without_exception_in_lead(self):
        from bot.condition_gate import lead_vs_exceptions
        body = ("<p>대신 과태료로 이행강제금 5회분을 내는데, 5회 미만으로 부과받았고 추가 위반이 없으면 이미 낸 이행강제금을 빼요.</p>"
                "<h2>FAQ</h2><p>전세사기피해자가 전세사기피해주택을 매수한 경우에는 이행강제금 5회분 과태료를 부과하지 않고, 추가 부설주차장 설치 의무도 지지 않아요.</p>")  # #928 실제 문장
        self.assertEqual(len(lead_vs_exceptions(body)), 1)
        fixed = body.replace("내는데,", "내요(전세사기피해주택은 제외).")
        self.assertEqual(lead_vs_exceptions(fixed), [])
        table = ("<p>품목분류 사전심사를 신청하면 신청받은 날부터 30일 안에 처리 기간이 끝나요.</p>"
                 "<p>처리 기간 신청받은 날부터 30일 (보정·분석·위원회 심의 기간 등 제외)</p>")
        self.assertEqual(lead_vs_exceptions(table), [])               # 괄호 속 '제외'는 예외 문장이 아님

    def test_writer_sees_it_and_apply_review_can_name_it(self):
        self.assertIn("lead_vs_exceptions", (ROOT / "bot" / "source_check.py").read_text(encoding="utf-8"))
        self.assertIn('"lead_vs_exceptions"', (ROOT / "automation" / "apply_review.py").read_text(encoding="utf-8"))


class ServerStatsTest(unittest.TestCase):
    """서버 상태 기록 (11/6 운영자 '기록 만들어라'): 같은 서버(2코어)의 사주데이가 배포 때 응답 없음 → 올릴지 숫자로."""

    def reader(self, load, saju=120):
        return {"load": lambda: load, "cores": lambda: 2, "saju": lambda: saju,
                "mem": lambda: {"total": 3900, "available": 900, "swap_used": 50}}

    def test_daily_aggregate_and_report_once(self):
        from datetime import datetime
        from bot import server_stats as ss
        state = {}
        self.assertIsNone(ss.record(state, datetime(2026, 11, 6, 10, 5), True, self.reader(5.14, None)))
        ss.record(state, datetime(2026, 11, 6, 10, 6), False, self.reader(0.8))
        day = state["server_stats"]["2026-11-06"]
        self.assertEqual((day["samples"], day["load_max"], day["load_max_at"]), (2, 5.14, "2026-11-06 10:05"))
        self.assertEqual((day["high_minutes"], day["high_at"]), (1, ["10:05"]))      # 코어당 1.5 넘음
        self.assertEqual((day["saju_checks"], day["saju_fail"], day["saju_fail_at"]), (1, 1, ["10:05"]))
        self.assertEqual(day["mem_avail_min"], 900)
        report = ss.record(state, datetime(2026, 11, 7, 0, 1), True, self.reader(0.5))
        self.assertIn("서버 상태(2026-11-06)", report)
        self.assertIn("높은 부하 1분(10:05)", report)
        self.assertIn("실패 1/1(10:05)", report)
        self.assertIsNone(ss.record(state, datetime(2026, 11, 7, 0, 2), False, self.reader(0.5)))   # 하루 한 번만

    def test_keeps_two_weeks(self):
        from datetime import datetime, timedelta
        from bot import server_stats as ss
        state, start = {}, datetime(2026, 11, 1, 12, 0)
        for i in range(20):
            ss.record(state, start + timedelta(days=i), False, self.reader(0.5))
        self.assertLessEqual(len(state["server_stats"]), ss.KEEP_DAYS + 1)

    def test_proc_readers(self):
        import tempfile
        from bot import server_stats as ss
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("MemTotal: 4000000 kB\nMemAvailable: 1024000 kB\nSwapTotal: 2048000 kB\nSwapFree: 1024000 kB\n")
        self.assertEqual(ss.read_mem(f.name), {"total": 3906, "available": 1000, "swap_used": 1000})
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("5.14 3.21 1.02 2/300 1234\n")
        self.assertEqual(ss.read_load(f.name), 5.14)
        self.assertIn("server_stats", (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8"))


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

    def test_written_items_do_not_crowd_out_new_ones(self):
        """11/7: 저장 60칸이 이미 쓴 항목으로 차서 새 글감 0 — 안 쓴 것을 앞에, 넉넉히 저장."""
        from bot import law_watch
        items = [{"key": f"done{i}"} for i in range(70)] + [{"key": "new1"}, {"key": "new2"}]
        got = law_watch.keep_upcoming(items, [f"done{i}" for i in range(70)])
        self.assertEqual([g["key"] for g in got[:2]], ["new1", "new2"])
        self.assertGreaterEqual(law_watch.UPCOMING_MAX, 150)

    def test_core_laws_cover_focus_topics(self):
        """11/7 운영자 '주제 좁히기 — 빠지는 거 없게': 일·4대보험·세금·복지 주요 법령을 시행 예정 글감으로 봄."""
        from bot import law_watch
        for name in ("산업재해보상보험법", "임금채권보장법", "고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률",
                     "국세기본법", "종합부동산세법", "긴급복지지원법", "주거급여법", "구직자 취업촉진 및 생활안정지원에 관한 법률"):
            self.assertIn(name, law_watch.CORE_LAWS)
        self.assertEqual(len(law_watch.CORE_LAWS), len(set(law_watch.CORE_LAWS)))

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


class SearchSummaryTest(unittest.TestCase):
    """11/8 검색 성과 요약: 검색어 없이 날짜·글 주소로 합계 (익명 검색어 포함), 하루 한 번만 조회."""

    def test_summarize_totals_weeks_and_top(self):
        from bot import search_console as sc
        daily = [{"date": f"2026-10-{d:02d}", "clicks": 1 if d > 20 else 0, "impressions": 10, "position": 20.0 if d > 20 else 40.0}
                 for d in range(1, 29)]
        pages = [{"page": "https://first-lab.kr/%EC%8B%A4%EC%97%85%EA%B8%89%EC%97%AC/", "clicks": 3, "impressions": 50, "position": 12.34},
                 {"page": "https://first-lab.kr/b/", "clicks": 0, "impressions": 90, "position": 30.0},
                 {"page": "https://first-lab.kr/c/", "clicks": 0, "impressions": 0, "position": 0.0}]
        s = sc.summarize(daily, pages, 120, "2026-11-08")
        self.assertEqual((s["clicks"], s["impressions"]), (8, 280))
        self.assertEqual(s["last7"], {"clicks": 7, "impressions": 70})
        self.assertEqual(s["prev7"], {"clicks": 1, "impressions": 70})
        self.assertEqual(s["pages_seen"], 2)
        self.assertEqual(s["top"][0]["page"], "/실업급여/")  # 클릭 많은 글 먼저, 주소는 읽을 수 있게
        self.assertEqual(s["top"][0]["position"], 12.3)
        self.assertAlmostEqual(s["position"], round((8 * 10 * 20 + 20 * 10 * 40) / 280, 1))
        line = sc.summary_line(s)
        self.assertIn("노출 280·클릭 8", line)
        self.assertIn("노출된 글 2/공개 120편", line)

    def test_no_impressions(self):
        from bot import search_console as sc
        s = sc.summarize([], [], None, "2026-11-08")
        self.assertIsNone(s["position"])
        self.assertIn("평균 -", sc.summary_line(s))

    def test_record_once_a_day(self):
        from unittest import mock
        from bot import search_console as sc
        state = {}
        with mock.patch.object(sc, "available", return_value=True), \
             mock.patch.object(sc, "fetch_totals", return_value=([], [])) as f, \
             mock.patch.object(sc, "_published_count", return_value=5):
            self.assertIn("검색(28일", sc.record(state, "2026-11-08"))
            self.assertEqual(sc.record(state, "2026-11-08"), "")  # 같은 날 두 번째는 조회 안 함
            sc.record(state, "2026-11-09")
        self.assertEqual(f.call_count, 2)
        self.assertEqual(state["search"]["date"], "2026-11-09")

    def test_maintenance_exposes_search(self):
        php = (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8")
        self.assertIn("'search'      => $state['search'] ?? null", php)


class SecondReviewTest(unittest.TestCase):
    """11/8 오류율 낮추기: 새 글은 2차 독립 검수("round": 2) 기록이 있어야 공개 대기열에 들어감."""

    def test_missing_round2_held(self):
        from bot.review_gate import second_review_missing
        self.assertTrue(second_review_missing([{"claim": "a", "verdict": "맞음", "action": "유지"}]))
        self.assertTrue(second_review_missing(None))
        self.assertEqual(second_review_missing([{"claim": "a", "round": 2}]), [])

    def test_only_intake_checks(self):
        src = (ROOT / "bot" / "publish_queue.py").read_text(encoding="utf-8")
        self.assertIn("second_review_missing", src)
        self.assertNotIn("second_review_missing", (ROOT / "bot" / "hold.py").read_text(encoding="utf-8"))  # 보류 고치기는 다시 안 막음

    def test_writer_instruction(self):
        self.assertIn('"round": 2', (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))


class LeadParenThresholdTest(unittest.TestCase):
    """11/8 #2066: 요약·첫 문단 '최대 5년' ↔ 본문 '최대 5년(특례 15년)' — 괄호 속 예외를 요약이 뺌."""

    def test_paren_alternative(self):
        from bot.condition_gate import lead_thresholds
        body = "<p>세무서 허가를 받아 최대 5년에 나눠 내요.</p><table><tr><td>기간</td><td>최대 5년(특례 15년)</td></tr></table>"
        self.assertTrue(lead_thresholds("담보를 내고 최대 5년 나눠 내요.", body))
        ok = "<p>세무서 허가를 받아 최대 5년(특례 재산은 15년)에 나눠 내요.</p><table><tr><td>기간</td><td>최대 5년(특례 15년)</td></tr></table>"
        self.assertEqual(lead_thresholds("최대 5년(특례 재산은 15년) 나눠 내요.", ok), [])


class KoreanHundredFormsTest(unittest.TestCase):
    """11/8 #2085: '1억 400만 원' ↔ 법령 '1억4백만원', '4,800만' ↔ '4천8백만원' 을 같은 값으로 (근거 대조 '페이지에 없음' 오탐)."""

    def test_forms(self):
        from bot.source_check import has_number, key_numbers
        self.assertEqual(key_numbers("공급대가 1억 400만 원 미만"), ["10400"])
        self.assertEqual(key_numbers("최대 1억 2,000만 원까지"), ["12000"])
        self.assertTrue(has_number("1억4백만원을 말한다", "10400"))
        self.assertTrue(has_number("4천8백만원 이상", "4800"))
        self.assertTrue(has_number("1억2천4백만원", "12400"))
        self.assertTrue(has_number("3천300만원", "3300"))
        self.assertFalse(has_number("1억4백만원", "10500"))


class DeployRetryTest(unittest.TestCase):
    """11/8 두 번: 배포 도구 내려받기가 GitHub 504 로 실패해 배포가 1시간 넘게 멈춤 → 1분 뒤 같은 단계를 한 번 더."""

    def test_retry_step_same_as_first(self):
        wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        deploy = wf.split("\n  deploy:")[1]
        first = deploy.split("      - name: Apply on server\n")[1].split("      - name: Wait before retry")[0]
        second = deploy.split("      - name: Apply on server (retry)\n")[1]
        body = lambda t: t[t.index("        uses: appleboy"):]
        self.assertIn("continue-on-error: true", first)
        self.assertIn("if: steps.apply.outcome == 'failure'", second)
        self.assertEqual(body(first).rstrip(), body(second).rstrip())  # 고칠 때 두 단계를 같이 고쳐야 함


class SearchQueriesTest(unittest.TestCase):
    def test_top_queries(self):
        from bot.search_console import top_queries
        rows = [{"query": "자동차보험료 계산 방법", "page": "https://first-lab.kr/%EC%9E%90%EB%8F%99%EC%B0%A8/", "clicks": 0, "impressions": 12, "position": 4.26},
                {"query": "b", "page": "https://first-lab.kr/b/", "clicks": 0, "impressions": 3, "position": 30.0}]
        q = top_queries(rows)
        self.assertEqual(q[0]["query"], "자동차보험료 계산 방법")
        self.assertEqual((q[0]["page"], q[0]["position"]), ("/자동차/", 4.3))


class UpcomingLawSourceTest(unittest.TestCase):
    """11/8 #2149: '/법령/국적법' 은 현행 판만 열려 시행 예정 개정의 '법률 제21725호' 가 '페이지에 없음' — 예정 판에서도 찾음."""

    def test_found_in_upcoming(self):
        from bot.source_check import check, FOUND, MISSING
        review = [{"claim": "2026년 6월 2일 공포 법률 제21725호", "verdict": "맞음", "action": "유지",
                   "urls": ["https://www.law.go.kr/법령/국적법"]}]
        page = lambda u: "현행 국적법 본문 " * 20
        r = check(review, fetcher=page, upcoming=lambda u: "<공포번호>21725</공포번호>")
        self.assertEqual(r["counts"][FOUND], 1)
        r = check(review, fetcher=page, upcoming=lambda u: "")
        self.assertEqual(r["counts"][MISSING], 1)


class CacheSurrogateTest(unittest.TestCase):
    """11/9 #2161: 행정규칙 본문의 짝 잃은 서로게이트 문자로 캐시 저장이 실패해 근거 대조가 통째로 멈춤."""

    def test_cached_strips_surrogates(self):
        import os
        from unittest import mock
        from bot import condition_gate as cg
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cg, "LAW_CACHE_DIR", Path(d)):
            got = cg._cached("rule", "x", lambda: "고시 본문 \ud83d 끝")
            self.assertEqual(got, "고시 본문  끝")
            self.assertEqual(cg._cached("rule", "x", lambda: "다시 안 부름"), "고시 본문  끝")  # 저장됐다가 읽힘


class LeavePromotionTest(unittest.TestCase):
    """11/9 #2179·#1471: 사용 촉진을 다 했으면 '보상 의무가 없어요' — 사용 기간이 끝나 소멸한 연차만 (근로기준법 제61조①)."""

    def test_missing_extinction(self):
        from bot.condition_gate import leave_promotion_extinction
        self.assertTrue(leave_promotion_extinction("<li>촉진을 모두 했는데도 쓰지 않은 연차는 보상 의무가 없어요.</li>"))
        self.assertEqual(leave_promotion_extinction("<li>촉진을 모두 했는데도 쓰지 않아 사용 기간이 끝나 없어진 연차는 보상 의무가 없어요.</li>"), [])
        self.assertEqual(leave_promotion_extinction("<p>퇴직금은 보상 의무가 없어요.</p>"), [])  # 연차 얘기가 아님


class AnalyticsTagTest(unittest.TestCase):
    """11/9 GA4: ID 형식이 맞을 때만 태그, 로그인한 운영자 방문은 빼고, 개인정보처리방침에 안내."""

    def test_plugin(self):
        php = (ROOT / "wordpress" / "mu-plugins" / "first-lab-analytics.php").read_text(encoding="utf-8")
        self.assertIn("is_user_logged_in()", php)
        self.assertIn("/^G-[A-Z0-9]{4,16}$/", php)
        self.assertIn("Google 애널리틱스", (ROOT / "wordpress" / "pages" / "privacy-policy.html").read_text(encoding="utf-8"))

    def test_tag_output(self):
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        code = ("define('ABSPATH','/');require 'wordpress/mu-plugins/first-lab-analytics.php';"
                "echo firstlab_ga4_tag('')==='' ? 'EMPTY' : 'BAD'; echo '|';"
                "echo strpos(firstlab_ga4_tag('G-ABC123XYZ9'),'gtag/js?id=G-ABC123XYZ9')!==false ? 'OK' : 'BAD';"
                "echo '|'; echo firstlab_ga4_tag('G-x\"><script>')==='' ? 'SAFE' : 'BAD';")
        out = subprocess.run(["php", "-r", code], cwd=ROOT, capture_output=True, text=True).stdout
        self.assertEqual(out, "EMPTY|OK|SAFE")


class GA4SummaryTest(unittest.TestCase):
    """11/9 GA4 방문 요약: 글별 참여·사이트 안 이동, 신호는 방문 30회 이상 글만, 실패하면 할 일을 남기고 그날은 다시 안 부름."""

    def test_summarize(self):
        from bot import ga4
        pages = [{"pagePath": "/%EC%8B%A4%EC%97%85%EA%B8%89%EC%97%AC/", "sessions": 40, "engagementRate": 0.2, "userEngagementDuration": 800, "screenPageViews": 50},
                 {"pagePath": "/b/", "sessions": 35, "engagementRate": 0.8, "userEngagementDuration": 3500, "screenPageViews": 40},
                 {"pagePath": "/c/", "sessions": 5, "engagementRate": 0.0, "userEngagementDuration": 0, "screenPageViews": 5}]
        moves = [{"pageReferrer": "https://first-lab.kr/b/", "pagePath": "/c/", "screenPageViews": 7},
                 {"pageReferrer": "https://www.google.com/", "pagePath": "/b/", "screenPageViews": 30},
                 {"pageReferrer": "https://first-lab.kr/b/", "pagePath": "/b/", "screenPageViews": 3}]  # 같은 글 새로고침은 이동 아님
        s = ga4.summarize(pages, moves, "2026-11-10")
        self.assertEqual(s["sessions"], 80)
        self.assertEqual(s["internal_moves"], 7)
        self.assertEqual(s["top_moves"][0], {"from": "/b/", "to": "/c/", "views": 7})
        self.assertEqual(s["low_engagement"], ["/실업급여/"])
        self.assertEqual(s["high_engagement"], ["/b/"])  # /c/ 는 방문 5회라 판단 안 함
        self.assertIn("세션 80", ga4.summary_line(s))

    def test_record_failure_tells_what_to_do(self):
        from unittest import mock
        from bot import ga4
        state = {}
        with mock.patch.object(ga4, "available", return_value=True), \
             mock.patch.object(ga4, "fetch", side_effect=RuntimeError("GA4 API 403: PERMISSION_DENIED")), \
             mock.patch.object(ga4, "service_email", return_value="bot@x.iam.gserviceaccount.com"):
            line = ga4.record(state, "2026-11-10")
            self.assertIn("bot@x.iam.gserviceaccount.com", line)
            self.assertIn("실패", line)
            self.assertIn("실패", ga4.record(state, "2026-11-10"))  # 실패한 날은 다음 실행 때 다시 시도
        with mock.patch.object(ga4, "available", return_value=True), \
             mock.patch.object(ga4, "fetch", return_value=([], [])) as f:
            self.assertIn("세션 0", ga4.record(state, "2026-11-10"))
            self.assertEqual(ga4.record(state, "2026-11-10"), "")  # 성공한 날은 다시 안 부름
            self.assertEqual(f.call_count, 1)

    def test_wired(self):
        self.assertIn("record_ga4", (ROOT / "bot" / "discover.py").read_text(encoding="utf-8"))
        self.assertIn("record_ga4", (ROOT / "server" / "bot-check.sh").read_text(encoding="utf-8"))
        self.assertIn("'ga4'         => $state['ga4'] ?? null", (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8"))


class FlowTest(unittest.TestCase):
    """11/9 운영자 '1·2번만 해라': 본문 중간 '이어서 보면 좋은 글'·관련 계산기, 공개 글 3편 이상 태그 = 주제 모음."""

    def _php(self, code: str) -> str:
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        boot = ("define('ABSPATH','/');function add_filter(){} function add_shortcode(){} "
                "require 'wordpress/mu-plugins/first-lab-related.php';require 'wordpress/mu-plugins/first-lab-flow.php';")
        return subprocess.run(["php", "-r", boot + code], cwd=ROOT, capture_output=True, text=True).stdout

    def test_insert_before_second_h2(self):
        out = self._php("echo firstlab_flow_insert_before_h2('<p>a</p><h2>1</h2><p>b</p><h2>2</h2>','[BOX]'); echo '|';"
                        "var_dump(firstlab_flow_insert_before_h2('<p>a</p><h2>1</h2>','[BOX]'));")
        self.assertTrue(out.startswith("<p>a</p><h2>1</h2><p>b</p>[BOX]\n<h2>2</h2>|"), out)
        self.assertIn("NULL", out)  # 소제목 하나뿐이면 넣지 않음

    def test_calc_by_record_and_hub(self):
        out = self._php(
            "$cs=[['id'=>'stock-average','title'=>'주식 평단가 계산기','url'=>'a'],['id'=>'capital-gains-tax','title'=>'양도소득세 계산기','url'=>'b']];"
            "$c=firstlab_flow_calc_by_id('capital-gains-tax',$cs);echo $c['url'];echo '|';"
            "var_dump(firstlab_flow_calc_by_id('',$cs));echo '|';"
            "$h=firstlab_hub_pick([['name'=>'홈택스','count'=>8,'url'=>'x'],['name'=>'연말정산','count'=>12,'url'=>'y'],['name'=>'월세','count'=>2,'url'=>'z']]);echo $h['url'];echo '|';"
            "var_dump(firstlab_hub_pick([['name'=>'월세','count'=>2,'url'=>'z']]));")
        parts = out.split("|")
        self.assertEqual(parts[0], "b")      # 서버가 글 내용으로 고른 계산기
        self.assertIn("NULL", parts[1])      # 기록이 없으면 안 붙임 (제목으로 짐작 안 함 — 11/9 자동차보험료 글에 산재보험료 계산기)
        self.assertEqual(parts[2], "y")      # 공개 글 많은 태그
        self.assertIn("NULL", parts[3])      # 2편뿐이면 모음 아님
        flow = (ROOT / "wordpress" / "mu-plugins" / "first-lab-flow.php").read_text(encoding="utf-8")
        self.assertNotIn("firstlab_related_score", flow)

    def test_calc_chosen_by_content(self):
        from bot import related_posts as r
        posts = [{"id": 1, "title": "자동차보험료 계산 방법", "content": "<p>자동차보험료는 보험사마다 다르고 운전자 나이, 차종, 사고 이력, 특약에 따라 견적이 달라져요.</p>"},
                 {"id": 2, "title": "개인사업자 부가세 신고기간", "content": "<p>부가가치세는 매출세액에서 매입세액을 빼서 계산해요. 일반과세자 간이과세자 신고 납부.</p>"},
                 {"id": 3, "title": "양도세 계산기 사용법", "content": '<p>[firstlab_calc id="capital-gains-tax"]</p>'}]
        calcs = [{"id": "calc:industrial-accident-insurance", "title": "산재보험료 계산기", "content": "업종별 산재보험료율 보수총액 사업주 부담 근로복지공단"},
                 {"id": "calc:vat", "title": "부가가치세 계산기", "content": "부가가치세 매출세액 매입세액 일반과세자 간이과세자 공급가액"}]
        got = r.flow_calcs(posts, calcs)
        self.assertEqual(got[1], "")         # '보험료'만 같은 산재보험료 계산기는 안 붙음
        self.assertEqual(got[2], "vat")
        self.assertEqual(got[3], "")         # 본문에 계산기가 이미 있으면 안 붙임
        self.assertEqual(len(r.calc_docs()), len(list((ROOT / "wordpress" / "themes" / "first-lab" / "calculators" / "defs").glob("*.js"))))

    def test_wired(self):
        self.assertIn("[firstlab_hub_intro]", (ROOT / "wordpress" / "themes" / "first-lab" / "templates" / "archive.html").read_text(encoding="utf-8"))
        rel = (ROOT / "wordpress" / "mu-plugins" / "first-lab-related.php").read_text(encoding="utf-8")
        self.assertIn("firstlab_flow_shown", rel)  # 본문 중간에 보여 준 글은 하단에서 뺌
        flow = (ROOT / "wordpress" / "mu-plugins" / "first-lab-flow.php").read_text(encoding="utf-8")
        self.assertIn("the_seo_framework_robots_meta_array", flow)  # 글 3편 미만 태그 페이지는 noindex

class DeadLinkRecheckTest(unittest.TestCase):
    """11/10 #1678: ols.semas.or.kr 이 서버에서 한 번 안 열려 '주소 연결 안 됨' 보류 — 실제로는 열리는 주소. 글쓰기가 멈춘 동안 평생 보류 안 되게
    한 번 더 기다려 보고(_exists 재시도), 한 시간마다 다시 열어 보고 열리면 보류를 푼다."""

    def test_recheck_releases_when_host_opens(self):
        from bot import official_links as ol
        reasons = {"1678": ol.dead_reason(["ols.semas.or.kr"]), "1700": "다른 이유", "1701": ol.dead_reason(["nope.go.kr"])}

        def wp(*args):
            if args[:2] == ("post", "list"):
                return json.dumps([{"ID": k} for k in reasons])
            if args[:3] == ("post", "meta", "get"):
                return reasons[args[3]]
            return ""
        released = []
        with mock.patch.object(ol, "_resolves", lambda h: False):
            out = ol.recheck_dead(wp, exists=lambda u: "semas" in u, release=released.append)
        self.assertEqual(released, [1678])  # 열린 주소만, 다른 보류 이유는 건드리지 않음
        self.assertIn("ols.semas.or.kr", out[0])

    def test_registered_name_is_not_dead(self):
        """이름이 등록된 공식 주소(nip.kdca.go.kr)는 서버에서 연결이 막혀도 '없는 주소'가 아님 — 이름도 없고 연결도 안 될 때만."""
        from bot import official_links as ol
        never = lambda u: False  # noqa: E731
        self.assertEqual(ol.dead_hosts(["nip.kdca.go.kr"], exists=never, resolves=lambda h: h == "nip.kdca.go.kr"), [])
        self.assertEqual(ol.dead_hosts(["nosuch.go.kr"], exists=never, resolves=lambda h: False), ["nosuch.go.kr"])

    def test_exists_retries_once(self):
        from bot import official_links as ol
        calls = []

        def urlopen(req, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise TimeoutError()
            return contextlib.nullcontext()
        import contextlib
        with mock.patch.object(ol.urllib.request, "urlopen", urlopen):
            self.assertTrue(ol._exists("https://ols.semas.or.kr"))
        self.assertEqual(calls[1], ol.DEAD_RETRY_TIMEOUT)


class DraftLinkHoldTest(unittest.TestCase):
    """11/9 운영자 '링크도 미리 초안 만들면 들어가 보고 맞게 링크 만들게 해야 되는 거 아냐?' — 공개 직전 본문 주소를 열어 보고,
    연결조차 안 되는 주소가 있으면 공개하지 않고 보류 (주소가 틀렸을 가능성). 봇 차단(403)은 주소가 있는 것으로 봄."""

    def test_host_with_korean_particle(self):
        from bot import official_links as o
        self.assertEqual(o.hosts_in("<p>license.kpc.or.kr에서 해요. wetax.go.kr로 내요.</p>"), ["license.kpc.or.kr", "wetax.go.kr"])

    def test_draft_intake_checks_links(self):
        src = (ROOT / "bot" / "publish_queue.py").read_text(encoding="utf-8")
        self.assertIn("official_links.run_for(post_id)", src)  # 초안 저장 때 주소 확인·기록 (공개 뒤 화면 점검 대신, 11/9 운영자)
        self.assertNotIn("screen_check", (ROOT / "bot" / "run.py").read_text(encoding="utf-8"))

    def test_waiting_draft_with_dead_link_is_held(self):
        from bot import official_links as o
        calls = []

        def wp(*a):
            calls.append(a)
            return json.dumps([{"id": 9, "content": "<p>nope.go.kr에서 신청</p>", "status": "draft"}]) if a[0] == "eval" else ""
        o.run(wp=wp, check=lambda u: False, today="2026-11-09", exists=lambda u: False)
        self.assertIn(("post", "meta", "update", "9", "_firstlab_hold", o.dead_reason(["nope.go.kr"])), calls)
        self.assertIn(("post", "meta", "delete", "9", "_firstlab_queue"), calls)  # 공개 대기에서 뺌

    def test_dead_vs_blocked(self):
        from bot import official_links as o
        self.assertEqual(o.dead_hosts(["nope.go.kr", "blocked.or.kr"], exists=lambda u: "blocked" in u), ["nope.go.kr"])

    def test_publish_holds_dead_link(self):
        from bot import schedule
        src = (ROOT / "bot" / "schedule.py").read_text(encoding="utf-8")
        self.assertIn("official_links.dead_reason(dead)", src)
        self.assertLess(src.index("official_links.run_for(candidate)"), src.index('"--post_status=publish"'))  # 공개 전에 확인
        self.assertIsNotNone(schedule)


class ContentRelatedTest(unittest.TestCase):
    """11/9 운영자 '제목으로 판단할 게 아니고 글 내용으로' — ITQ 조회 글에 등기부등본 발급 글이 붙었음 ('발급' 하나 겹침)."""

    @staticmethod
    def post(pid, title, body):
        return {"id": pid, "title": title, "content": f"<p>{body}</p>"}

    def test_content_decides(self):
        from bot import related_posts as r
        posts = [
            self.post(1, "ITQ 자격증 조회 방법: 발급비 7,400원", "한국생산성본부 KPC 자격 사이트에서 ITQ 합격 여부와 점수, 엑셀 한글 파워포인트 등급을 조회하고 자격증을 발급해요."),
            self.post(2, "건물 등기부등본 발급 방법과 수수료", "인터넷등기소에서 건물 등기사항증명서를 열람하거나 발급해요. 소유권 근저당 갑구 을구를 확인해요."),
            self.post(3, "엑셀 자격증 비교: 컴활 2급·ITQ 엑셀", "컴퓨터활용능력 2급과 ITQ 엑셀 등급, 한국생산성본부 KPC 자격, 합격 점수와 응시료를 비교해요."),
            self.post(4, "주휴수당 뜻", "주 15시간 이상 일하고 개근하면 유급휴일 하루치 임금을 받아요. 알바도 주휴수당을 받아요."),
            self.post(5, "주휴수당 계산", "주휴수당은 1주 소정근로시간을 40으로 나눈 값에 8과 시급을 곱해요. 주 15시간 이상 알바도 받아요."),
        ]
        rel = r.related(posts)
        self.assertNotIn(2, rel[1])          # '발급'만 겹치는 등기부등본은 안 붙음
        self.assertEqual(rel[1][:1], [3])    # 같은 KPC·ITQ 엑셀 내용
        self.assertEqual(rel[4][:1], [5])
        self.assertEqual(rel[2], [])         # 비슷한 글이 없으면 없음

    def test_screen_uses_only_record(self):
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        code = ("<?php define('ABSPATH', 1); function add_filter() {}\n"
                f"require '{ROOT / 'wordpress' / 'mu-plugins' / 'first-lab-related.php'}';\n"
                "echo json_encode(firstlab_related_from_meta('{\"ids\":[3,9,5]}', fn($id) => $id !== 9)), json_encode(firstlab_related_from_meta('', fn($id) => true));")
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(code)
        try:
            out = subprocess.run(["php", f.name], capture_output=True, text=True, check=True).stdout
        finally:
            import os
            os.unlink(f.name)
        self.assertEqual(out, "[3,5][]")     # 공개 아닌 글은 빼고, 기록이 없으면 관련 글 없음 (제목으로 짐작 안 함)



class PolicyWatchTest(unittest.TestCase):
    """11/9 운영자 '법이 아닌 새 사업·정책 잡을 방법 강구해라' — 정책브리핑 보도자료에서 개인 지원·제도만 글감으로."""

    PAGE = '''
<li><a href="/briefing/pressReleaseView.do?newsId=156788001&amp;pageIndex=1">
<span class="text"><strong>전세사기피해자 최소보장제 시행, 10일부터 최소지원금 신청하세요!</strong>
<span class="lead">전세사기피해자에게 최소지원금을 지급하는 제도가 10일부터 시행돼 신청을 받는다.</span>
<span class="source"><span>2026.11.09</span><span>국토교통부</span></span></span></a></li>
<li><a href="/briefing/pressReleaseView.do?newsId=156788002&amp;pageIndex=1">
<span class="text"><strong>2차관, 국제수로기구 사무총장 접견</strong>
<span class="lead">지원 협력 신청 방안을 논의했다.</span>
<span class="source"><span>2026.11.09</span><span>외교부</span></span></span></a></li>
<li><a href="/briefing/pressReleaseView.do?newsId=156700000&amp;pageIndex=1">
<span class="text"><strong>청년 월세 지원 신청 접수 시작</strong>
<span class="lead">신청하세요.</span>
<span class="source"><span>2026.10.01</span><span>국토교통부</span></span></span></a></li>'''

    def test_parse_and_pick(self):
        import datetime as dt
        from bot import policy_watch as p
        items = p.parse(self.PAGE)
        self.assertEqual([i["key"] for i in items], ["156788001", "156788002", "156700000"])
        self.assertEqual(items[0]["agency"], "국토교통부")
        picked = p.pick(items, set(), dt.date(2026, 11, 9))
        self.assertEqual([i["key"] for i in picked], ["156788001"])  # 외교 접견은 빼고, 지난 보도자료(10/1)도 뺌
        self.assertEqual(p.pick(items, {"156788001"}, dt.date(2026, 11, 9)), [])  # 쓴 것·건너뛴 것은 다시 안 줌

    def test_wired(self):
        self.assertIn('"policy_watch"', (ROOT / "bot" / "discover.py").read_text(encoding="utf-8"))
        self.assertIn("'new_policies'", (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8"))
        self.assertIn("new_policies_done", (ROOT / "bot" / "publish_queue.py").read_text(encoding="utf-8"))
        self.assertIn("new_policies", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))


class NewLawWatchTest(unittest.TestCase):
    """11/9 운영자 '그것도 해라': 아직 안 다룬 분야의 곧 시행될 법령 — 법제처 시행 예정 목록에서 생활 분야 제정·개정만."""

    LIST = ('<LawSearch><law><법령명한글>소상공인 보호 및 지원에 관한 법률</법령명한글><법령일련번호>1</법령일련번호><시행일자>20261127</시행일자>'
            '<제개정구분명>일부개정</제개정구분명><법령구분명>법률</법령구분명><소관부처명>중소벤처기업부</소관부처명></law>'
            '<law><법령명한글>가나 법</법령명한글><법령일련번호>2</법령일련번호><시행일자>20261127</시행일자>'
            '<제개정구분명>타법개정</제개정구분명><법령구분명>법률</법령구분명><소관부처명>고용노동부</소관부처명></law>'
            '<law><법령명한글>근로기준법</법령명한글><법령일련번호>3</법령일련번호><시행일자>20261201</시행일자>'
            '<제개정구분명>일부개정</제개정구분명><법령구분명>법률</법령구분명><소관부처명>고용노동부</소관부처명></law>'
            '<law><법령명한글>국방 법</법령명한글><법령일련번호>4</법령일련번호><시행일자>20261201</시행일자>'
            '<제개정구분명>일부개정</제개정구분명><법령구분명>법률</법령구분명><소관부처명>국방부</소관부처명></law></LawSearch>')
    REASON = ('<Law><제개정이유><제개정이유내용>◇ 개정이유 폐업하려는 소상공인에게 심리 상담과 법률 지원을 하고, 신청 절차를 정함'
              '</제개정이유내용></제개정이유></Law>')

    def fake(self, kind, target, **kw):
        return self.LIST if kind == "search" else self.REASON

    def test_pick_uncovered_life_laws(self):
        import datetime as dt
        from bot import new_law_watch as w, law_api
        rows = w.upcoming(dt.date(2026, 11, 9), self.fake)
        cands = w.candidates(rows, {"근로기준법"}, set())
        self.assertEqual([c["name"] for c in cands], ["소상공인 보호 및 지원에 관한 법률"])  # 타법개정·이미 다룬 법·국방 빼고
        self.assertGreaterEqual(w.score(cands[0], w.reason_of(cands[0], self.fake)), w.MIN_SCORE)
        self.assertEqual(w.score(cands[0], "용어를 알기 쉽게 정비하려는 것임"), 0)  # 자구 정비는 글감 아님
        state = {}
        with mock.patch.object(law_api, "available", return_value=True), \
                mock.patch("bot.state.load_state", return_value=state), mock.patch("bot.state.save_state"), \
                mock.patch("bot.state.add_log"):
            w.run(dt.date(2026, 11, 9), self.fake, covered={"근로기준법"})
        self.assertEqual([x["key"] for x in state["new_laws"]], ["소상공인 보호 및 지원에 관한 법률|20261127"])

    def test_wired(self):
        self.assertIn('"new_law_watch"', (ROOT / "bot" / "discover.py").read_text(encoding="utf-8"))
        self.assertIn("'new_laws'", (ROOT / "wordpress" / "mu-plugins" / "first-lab-api.php").read_text(encoding="utf-8"))
        self.assertIn("new_laws_done", (ROOT / "bot" / "publish_queue.py").read_text(encoding="utf-8"))
        self.assertIn("new_laws", (ROOT / "automation" / "daily-writer.md").read_text(encoding="utf-8"))


class PageResaveTest(unittest.TestCase):
    """11/9 다음 검색등록 '홍보/스팸성' 보류: 배포마다 고정·계산기 페이지 약 60개를 다시 저장해 사이트맵 마지막 수정일이 하루 여러 번 바뀜
    (자동 생성 사이트처럼 보임) — 원본이 같으면 갱신하지 않음."""

    def test_skip_unchanged_pages(self):
        sh = (ROOT / "server" / "wp-setup.sh").read_text(encoding="utf-8")
        self.assertIn("_firstlab_src_hash", sh)
        self.assertLess(sh.index('grep -qx "$id $src_hash"'), sh.index('wp post update "$id" --post_status="$status"'))



class ReadDecorateTest(unittest.TestCase):
    """11/9 운영자 '가독성 좋게 컬러나 선': 첫 본문 문단=요약 상자, '다만'=주의 상자, 자주 묻는 질문=Q (화면에서 class 만)."""

    def test_decorate(self):
        import shutil
        import subprocess
        if not shutil.which("php"):
            self.skipTest("php 없음")
        html = ('<nav><ol><li><a href="#t">자주 묻는 질문</a></li></ol></nav><p class="meta">작성 2026년 11월 7일</p><p>주휴수당은 1주 동안 일하기로 한 날을 모두 나온 근로자에게 주는 유급 주휴일의 하루치 임금이에요.</p>'
                '<p>다만 예외예요.</p><h3>본문 소제목</h3><p><strong>이런 건 꼭 확인하세요</strong></p>\n\n<ul class="wp-block-list"><li>결근</li></ul>'
                '<p><strong>한 줄 정리:</strong> 개근하면 받아요.</p><h2>자주 묻는 질문</h2><h3>질문?</h3>')
        code = ("<?php define('ABSPATH', 1); function add_filter() {} function add_action() {}\n"
                f"require '{ROOT / 'wordpress' / 'mu-plugins' / 'first-lab-readability.php'}';\n"
                "echo firstlab_read_decorate(getenv('H'));")
        import os
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as f:
            f.write(code)
        try:
            out = subprocess.run(["php", f.name], capture_output=True, text=True, check=True, env={**os.environ, "H": html}).stdout
        finally:
            os.unlink(f.name)
        self.assertIn('<p class="meta">작성', out)                       # 작성일 줄은 상자 아님 (11/9 시안에서 잘못 붙었음)
        self.assertIn('<p class="fl-lead">주휴수당은', out)
        self.assertIn('class="fl-note', out)
        self.assertIn("<h3>본문 소제목</h3>", out)
        self.assertIn('<h3 class="fl-q', out)
        self.assertIn('<div class="fl-check"><p class="fl-check-title">이런 건 꼭 확인하세요</p><ul class="wp-block-list"><li>결근</li></ul></div>', out)  # 11/10
        self.assertIn('<p class="fl-sum ">', out)


class PlainCheckTest(unittest.TestCase):
    """11/9 운영자 '기존 글도 쉬운 말로 — 니가 직접': 다듬은 본문의 숫자·법 조항·링크·계산기·소제목·표가 원래와 같아야 반영."""

    OLD = ('<p>주휴수당은 근로기준법 제55조에 따라 1주 15시간 이상 일하면 받아요.</p><h2>얼마</h2>'
           '<p>시급 10,320원이면 82,560원이에요. <a href="https://www.moel.go.kr">고용노동부</a></p>')

    def test_same_facts_pass_and_changed_fail(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import plain_check as pc
        new = ('<p>일주일 개근하면 하루치 돈을 더 받아요.</p><p>근로기준법 제55조에 따라 1주 15시간 이상 일하면 주휴수당을 받아요.</p>'
               '<h2>얼마</h2><p>시급 10,320원이면 82,560원이에요. <a href="https://www.moel.go.kr">고용노동부</a></p>')
        self.assertEqual(pc.problems(self.OLD, new), [])
        self.assertTrue(any("숫자" in p for p in pc.problems(self.OLD, new.replace("82,560", "82,650"))))
        self.assertTrue(any("법 조항" in p for p in pc.problems(self.OLD, new.replace("제55조", "제56조"))))
        self.assertTrue(any("링크" in p for p in pc.problems(self.OLD, new.replace("moel", "mohw"))))
        self.assertTrue(any("첫 문장" in p for p in pc.problems(self.OLD, self.OLD)))  # 원래 첫 문장엔 법 조항 — 쉬운 한 줄로
        links = '<h2>공식 안내 바로가기</h2><ul><li><a href="https://x.go.kr">근로기준법 제55조 (휴일)</a></li><li><a href="https://y.go.kr">시행령 (주휴)</a></li></ul>'
        self.assertFalse(any("괄호" in p for p in pc.problems(self.OLD + links, new + links)))  # 글 끝 링크 목록은 문장 검사에서 뺌
        self.assertTrue(any("괄호" in p for p in pc.problems(self.OLD, new.replace("주휴수당을 받아요", "주휴수당(유급)을 받아요(법)"))))

    def test_long_paragraphs_split(self):
        """11/10 운영자 '줄바꿈은 엿 바꿔 먹었나' — 한눈에 보기 상자에 6문장 한 덩어리. 문단은 문장 2개까지, 넘으면 자동으로 끊음."""
        sys.path.insert(0, str(ROOT / "automation"))
        import plain_check as pc
        lead = "<p>첫 줄이에요. 둘째예요. 셋째예요.</p>"
        body = "<p>하나예요. <strong>둘</strong>이에요. 셋이에요. 넷이에요. 다섯이에요.</p><p class=\"fl-basis\">근거: 법</p>"
        self.assertEqual(len(pc.crowded_paragraphs(lead + body)), 2)
        out = pc.split_long_paragraphs(lead + body)
        self.assertEqual(out.count("<p>"), 1 + 3)  # 첫 문단(상자)은 사람이 <br> 로 나눔 — 그대로, 본문 5문장 → 2·2·1
        self.assertEqual(pc.crowded_paragraphs(out), ["첫 줄이에요. 둘째예요. 셋째예요."])
        self.assertEqual(pc.crowded_paragraphs("<p>첫 줄이에요.<br>둘째예요. 셋째예요.</p>"), [])
        self.assertEqual(pc.split_long_paragraphs("<p>x</p><p><strong>가요. 나요.</strong> 다요.</p>"), "<p>x</p><p><strong>가요. 나요.</strong> 다요.</p>")  # 태그 걸치면 안 끊음

    def test_search_terms_kept(self):
        """11/9 운영자 '검색에도 걸려야 되니 기존 단어 다 없애면 안 된다' — #1419 첫 다듬기에서 소정근로시간 9→3, 소정근로일 6→1."""
        sys.path.insert(0, str(ROOT / "automation"))
        import plain_check as pc
        old = "<h2>주휴수당 조건</h2>" + "<p>소정근로시간이 15시간 이상이에요.</p>" * 4
        easy = "<h2>주휴수당 조건</h2><p>소정근로시간은 계약한 시간이에요.</p>" + "<p>계약한 시간이 15시간 이상이에요.</p>" * 3
        self.assertIn("소정근로시간 4→1", pc.lost_terms(old, easy))
        kept = "<h2>주휴수당 조건</h2><p>소정근로시간은 계약한 시간이에요.</p>" + "<p>소정근로시간이 15시간 이상이에요.</p>" * 2
        self.assertEqual(pc.lost_terms(old, kept), [])
        self.assertIn("조건 1→0", pc.lost_terms(old, kept.replace("조건", "받는 법")))  # 소제목 낱말도


class NaverAdsDiagnoseTest(unittest.TestCase):
    """11/9: 키워드 도구 403 — 서버 키가 운영자 키와 같은지 비교하려고 끝 4자리는 비공개 state 에만, 공개 배포 기록엔 안 찍음."""

    def test_diagnose_keeps_tail_private(self):
        import io
        import urllib.error
        from bot import naver_ads as n

        class Fake(n.NaverAdsClient):
            def keyword_stats(self, hints):
                raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b'{"detail":"Invalid API key"}'))
        r = n.diagnose(Fake("1234567", "xxxxbbb9", "s" * 20))
        self.assertEqual((r["ok"], r["status"], r["license_tail"]), (False, 403, "bbb9"))
        self.assertIn("Invalid API key", r["message"])
        fake = "01000000" + "ab12" * 16  # 가짜 키 — 11/9: 네이버 오류 문구에 보낸 키 전체가 들어 있었음 → 가림

        class Leaky(n.NaverAdsClient):
            def keyword_stats(self, hints):
                raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(f'{{"title":"Invalid API-KEY","detail":"API-KEY {fake} is invalid."}}'.encode()))
        r = n.diagnose(Leaky("1234567", "xxxxbbb9", "s" * 20))
        self.assertNotIn(fake[:20], r["message"])
        self.assertIn("Invalid API-KEY", r["message"])
        src = (ROOT / "bot" / "naver_ads.py").read_text(encoding="utf-8")
        main = src[src.index('if __name__ == "__main__"'):]
        self.assertNotIn("license_tail", main.split("print(")[-1])  # 화면(공개 배포 기록)에는 키 끝자리 안 찍음


class DeploySpeedTest(unittest.TestCase):
    """11/9 운영자 '배포 왜 이렇게 오래 걸려': 전체 글을 한 편씩 읽는 보정은 배포를 기다리게 하지 않고 잠금 걸어 따로 실행."""

    def test_full_scan_runs_in_background(self):
        maint = (ROOT / "server" / "maint.sh").read_text(encoding="utf-8")
        for mod in ("bot.fix_posts", "bot.patch_check", "bot.migrate_photos", "bot.pending_images"):
            self.assertIn(mod, maint)
        for f in ("bot-setup.sh", "quick-deploy.sh"):
            sh = (ROOT / "server" / f).read_text(encoding="utf-8")
            self.assertIn('setsid nohup flock /var/lib/first-lab/bot/maint.lock bash "$REPO_DIR/server/maint.sh"', sh)
            self.assertNotIn('-m bot.fix_posts)', sh)  # 배포 안에서 직접 돌리지 않음
            self.assertLess(sh.index("bot.publish_queue"), sh.index("maint.sh"))  # 새 글·갱신 파일은 배포 안에서 먼저

    def test_quick_deploy_only_when_safe(self):
        """11/9 운영자 '이렇게 비효율적으로 해야 돼?': 글·화면·봇 코드만 바뀌면 빠른 배포, 서버 설정·워크플로·페이지·패키지·그날 첫 배포는 전체."""
        import subprocess
        wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        self.assertEqual(wf.count('bash "$REPO/server/quick-deploy.sh" "$CHANGED"'), 2)  # 다시 시도 단계도 같게
        cond = next(x.strip() for x in wf.splitlines() if "grep -qE '^(FULL|server/" in x)
        test = cond.replace("if ", "", 1).rsplit("; then", 1)[0]
        def full(changed, mark_today=True):
            script = (f'FULL_MARK=$(mktemp); {"date +%F > $FULL_MARK" if mark_today else ":"}; CHANGED={changed!r}; '
                      f'if {test}; then echo full; else echo quick; fi')
            return subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout.strip()
        self.assertEqual(full("wordpress/queue/a.json\nbot/run.py"), "quick")
        self.assertEqual(full("wordpress/mu-plugins/x.php"), "quick")
        self.assertEqual(full("server/setup.sh"), "full")
        self.assertEqual(full("wordpress/pages/about.html"), "full")
        self.assertEqual(full("FULL"), "full")
        self.assertEqual(full("bot/run.py", mark_today=False), "full")  # 그날 첫 배포
