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


class FallbackTest(unittest.TestCase):
    def test_direct_post_today(self):
        from bot.run import direct_post_today
        log = [{"time": "2026-11-05 08:20", "ok": True, "msg": "직접 작성 글: [발행] 'x'"}]
        self.assertTrue(direct_post_today({"log": log}, "2026-11-05"))
        self.assertFalse(direct_post_today({"log": log}, "2026-11-06"))
        bad = [{"time": "2026-11-05 08:20", "ok": False, "msg": "직접 작성 글 실패 (a.json)"}]
        self.assertFalse(direct_post_today({"log": bad}, "2026-11-05"))

    def test_write_mode(self):
        from bot.run import yields_to_direct
        log = {"log": [{"time": "2026-11-05 08:20", "ok": True, "msg": "직접 작성 글: [발행] 'x'"}]}
        direct, bot = BotSettings.from_option({}, {}), BotSettings.from_option({"write_mode": "bot"}, {})
        self.assertEqual((direct.write_mode, bot.write_mode), ("direct", "bot"))
        self.assertTrue(yields_to_direct(direct, log, "2026-11-05"))
        self.assertFalse(yields_to_direct(bot, log, "2026-11-05"))  # 봇 방식은 직접 글이 있어도 그대로 씀


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

    def test_patches_file_valid(self):
        for p in json.loads((ROOT / "wordpress" / "patches.json").read_text(encoding="utf-8")):
            self.assertTrue({"post", "why", "find", "replace_paragraph"} <= set(p))

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


if __name__ == "__main__":
    unittest.main()
