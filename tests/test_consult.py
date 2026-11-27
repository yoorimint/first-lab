"""법무사 상담 문의: 텔레그램 전송·보관 기간 삭제 (bot/consult_notify.py) + 워드프레스 쪽 검사 (tests/php/test_consult.php)."""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import unittest
import urllib.parse
from datetime import datetime
from pathlib import Path

from bot import consult_notify as C

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "wordpress" / "mu-plugins" / "first-lab-consult.php"


class FakeWP:
    """WP-CLI 흉내: 문의 글 목록과 메타."""

    def __init__(self, rows):
        self.rows = {r["ID"]: dict(r) for r in rows}
        self.meta: dict[int, str] = {}
        self.deleted: list[int] = []

    def __call__(self, *args):
        if args[:2] == ("post", "list"):
            if "--meta_compare=NOT EXISTS" in args:
                out = [r for i, r in sorted(self.rows.items()) if i not in self.meta]
            else:
                out = list(self.rows.values())
            return json.dumps(out)
        if args[:3] == ("post", "meta", "update"):
            self.meta[int(args[3])] = args[5]
            return ""
        if args[:2] == ("post", "delete"):
            self.deleted.append(int(args[2]))
            self.rows.pop(int(args[2]), None)
            return ""
        raise AssertionError(args)


def opener_ok(sent):
    def _open(req, timeout=10):
        sent.append(req.data.decode())
        return io.BytesIO(b'{"ok": true}')
    return _open


def opener_fail(req, timeout=10):
    raise OSError("down")


ROW = {"ID": 7, "post_title": "[상속] 홍길동", "post_content": "분야: 상속\n연락처: 010-1234-5678\n\n상속 등기 문의", "post_date": "2026-11-27 10:00:00"}
KEYS = {C.TOKEN_KEY: "t", C.CHAT_KEY: "c"}


class ConsultNotifyTest(unittest.TestCase):
    def test_sends_and_marks(self):
        wp, sent = FakeWP([ROW]), []
        logs = C.run(datetime(2026, 11, 27, 10, 1), KEYS, wp, opener_ok(sent))
        self.assertEqual(len(sent), 1)
        body = urllib.parse.parse_qs(sent[0])
        self.assertEqual(body["chat_id"], ["c"])
        self.assertIn("010-1234-5678", body["text"][0])
        self.assertIn(7, wp.meta)
        self.assertEqual(logs, [(True, "상담 문의 1건 텔레그램 전송")])
        self.assertEqual(C.run(datetime(2026, 11, 27, 10, 3), KEYS, wp, opener_ok(sent)), [])   # 보낸 건 다시 안 보냄
        self.assertEqual(len(sent), 1)

    def test_failure_retries_next_minute(self):
        wp = FakeWP([ROW])
        logs = C.run(datetime(2026, 11, 27, 10, 1), KEYS, wp, opener_fail)
        self.assertNotIn(7, wp.meta)
        self.assertFalse(logs[0][0])
        sent = []
        C.run(datetime(2026, 11, 27, 10, 3), KEYS, wp, opener_ok(sent))
        self.assertEqual(len(sent), 1)
        self.assertIn(7, wp.meta)

    def test_missing_keys_waits_and_logs_hourly(self):
        wp = FakeWP([ROW])
        self.assertEqual(C.run(datetime(2026, 11, 27, 10, 5), {}, wp, opener_fail), [])     # 매분 기록 안 쌓임
        logs = C.run(datetime(2026, 11, 27, 11, 0), {}, wp, opener_fail)
        self.assertEqual(len(logs), 1)
        self.assertIn("텔레그램 키 없음", logs[0][1])
        self.assertNotIn(7, wp.meta)    # 키를 넣으면 밀린 것부터 보냄

    def test_purge_after_retain_days_only(self):
        old = dict(ROW, ID=1, post_date="2026-10-01 09:00:00")
        new = dict(ROW, ID=2, post_date="2026-11-20 09:00:00")
        bad = dict(ROW, ID=3, post_date="날짜 아님")
        wp = FakeWP([old, new, bad])
        logs = C.run(datetime(2026, 11, 27, 10, 2), {}, wp, opener_fail)   # 10분마다(분 % 10 == 2) 정리
        self.assertEqual(wp.deleted, [1])          # 날짜를 못 읽은 건 지우지 않음
        self.assertIn((True, f"상담 문의 1건 삭제 (보관 {C.RETAIN_DAYS}일 지남)"), logs)

    def test_retain_days_match_php_consent(self):
        """동의문에 적는 보관 기간(PHP)과 실제로 지우는 기간(봇)이 같아야 한다."""
        m = re.search(r"FIRSTLAB_CONSULT_RETAIN_DAYS\s*=\s*(\d+)", PLUGIN.read_text(encoding="utf-8"))
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), C.RETAIN_DAYS)

    def test_banner_off_until_configured(self):
        """켜면 사무실 이름은 꼭, 전화는 넣었으면 올바른 번호. 분야는 디딤돌 문의창(toss-didim/src/consult.js)과 같은 목록."""
        spec = json.loads((ROOT / "wordpress" / "themes" / "first-lab" / "consult.json").read_text(encoding="utf-8"))
        if spec.get("enabled"):
            self.assertTrue(spec["office"]["name"].strip())
            phone = re.sub(r"\D", "", spec["office"].get("phone", ""))
            self.assertTrue(phone == "" or re.fullmatch(r"0\d{8,10}", phone))
            self.assertTrue(spec["services"])


@unittest.skipUnless(shutil.which("php"), "php 없음")
class ConsultPhpTest(unittest.TestCase):
    def test_php_checks(self):
        r = subprocess.run(["php", str(ROOT / "tests" / "php" / "test_consult.php"), str(PLUGIN)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
