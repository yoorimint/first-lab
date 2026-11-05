"""한국은행 경제통계(ECOS) API — 기준금리·은행 대출금리 (11/5 운영자 신청).

쓰는 곳: 대출·이자 글과 계산기의 금리 예시가 낡지 않았는지 (기준금리가 바뀌면 근거 글 재검증, 글쓰기 세션에 최신 값 전달).
키는 서버 비밀값 BOK_ECOS_KEY (set-secrets.sh). 키가 없거나 실패하면 None — 다른 기능에 영향 없음.

확인용(서버): python3 -m bot.bok_api            → 기준금리 최신값
             python3 -m bot.bok_api items 121Y006 → 그 통계의 항목 코드 목록
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.parse
import urllib.request

from .config import SECRETS_FILE, load_secrets

KEY_SECRET = "BOK_ECOS_KEY"
BASE = "https://ecos.bok.or.kr/api"
BASE_RATE = ("722Y001", "0101000")  # 한국은행 기준금리 (통계표, 항목)
TIMEOUT = 20


def _key() -> str:
    try:
        return load_secrets(SECRETS_FILE).get(KEY_SECRET, "") if SECRETS_FILE.exists() else ""
    except OSError:
        return ""


def _get(service: str, *parts: str, start: int = 1, end: int = 100) -> dict | None:
    key = _key()
    if not key:
        return None
    path = "/".join(urllib.parse.quote(str(p), safe="") for p in (service, key, "json", "kr", start, end, *parts))
    try:
        with urllib.request.urlopen(f"{BASE}/{path}", timeout=TIMEOUT) as res:  # noqa: S310 — 고정된 한국은행 주소
            got = json.loads(res.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return got.get(service)  # 오류면 {"RESULT": {...}} 만 와서 None


def series(stat: str, item: str, cycle: str, start: str, end: str) -> list[tuple[str, float]]:
    """[(기간 'YYYYMMDD'|'YYYYMM', 값)] 오래된 순. 못 받으면 []."""
    got = _get("StatisticSearch", stat, cycle, start, end, item, end=1000)
    out = []
    for row in (got or {}).get("row", []):
        try:
            out.append((row["TIME"], float(row["DATA_VALUE"])))
        except (KeyError, ValueError):
            continue
    return out


def base_rate(today: dt.date | None = None) -> tuple[str, float] | None:
    """기준금리 최신값 (날짜 'YYYYMMDD', %)."""
    today = today or dt.date.today()
    rows = series(*BASE_RATE, "D", f"{today - dt.timedelta(days=400):%Y%m%d}", f"{today:%Y%m%d}")
    return rows[-1] if rows else None


def items(stat: str) -> list[tuple[str, str, str]]:
    """통계표의 항목 (코드, 이름, 주기) — 처음 연결할 때 코드 찾기용."""
    got = _get("StatisticItemList", stat, end=500)
    return [(r.get("ITEM_CODE", ""), r.get("ITEM_NAME", ""), r.get("CYCLE", "")) for r in (got or {}).get("row", [])]


def main(argv: list[str]) -> int:
    if not _key():
        print("한국은행 API: 키 없음")
        return 1
    if argv[1:2] == ["items"] and len(argv) > 2:
        for code, name, cycle in items(argv[2]):
            print(f"{code}\t{cycle}\t{name}")
        return 0
    got = base_rate()
    print(f"한국은행 API: {'정상 — 기준금리 ' + str(got[1]) + '% (' + got[0] + ')' if got else '실패'}")
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
