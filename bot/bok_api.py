"""한국은행 경제통계(ECOS) API — 기준금리·은행 대출금리 (11/5 운영자 신청).

쓰는 곳: 대출·이자 글과 계산기의 금리 예시가 낡지 않았는지 (기준금리가 바뀌면 근거 글 재검증, 글쓰기 세션에 최신 값 전달).
키는 서버 비밀값 BOK_ECOS_KEY (set-secrets.sh). 키가 없거나 실패하면 None — 다른 기능에 영향 없음.

매일: bot/discover.py 끝에서 run() — 최신 금리를 상태(rates)에 저장, 기준금리가 바뀌면 관련 공개 글 재검증 표시.
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


LOAN_RATES = {  # 예금은행 가중평균 대출금리(신규취급액 기준, 연%, 월) — 통계표 121Y006 항목 (11/5 서버에서 항목 목록으로 확인)
    "mortgage": ("121Y006", "BECBLA0302", "주택담보대출"),
    "credit": ("121Y006", "BECBLA03051", "일반신용대출"),
    "household": ("121Y006", "BECBLA03", "가계대출"),
}
RATE_WORDS = ("기준금리",)  # 이 말이 든 공개 글은 기준금리가 바뀌면 재검증 대상


def latest_rates(today: dt.date | None = None) -> dict:
    """{base: {date, value}, mortgage|credit|household: {month, value, name}} — 못 받은 것은 빠짐."""
    today = today or dt.date.today()
    out = {}
    base = base_rate(today)
    if base:
        out["base"] = {"date": base[0], "value": base[1]}
    start = f"{today - dt.timedelta(days=200):%Y%m}"
    for key, (stat, item, name) in LOAN_RATES.items():
        rows = series(stat, item, "M", start, f"{today:%Y%m}")
        if rows:
            out[key] = {"month": rows[-1][0], "value": rows[-1][1], "name": name}
    return out


def run(today: dt.date | None = None) -> str:
    """매일(키워드 수집 끝): 최신 금리를 상태에 저장 → 글쓰기 세션이 /next 의 rates 로 봄.
    기준금리가 바뀌었으면 '기준금리'가 든 공개 글에 재검증 표시 (bot/refresh.py 가 가장 먼저 수치 재검증)."""
    from .law_watch import LAW_META
    from .settings import wp_cli
    from .state import add_log, load_state, save_state
    today = today or dt.date.today()
    if not _key():
        return "금리(한국은행): 키 없음 → 건너뜀"
    rates = latest_rates(today)
    if not rates:
        return "금리(한국은행): 받지 못함"
    state = load_state()
    old = ((state.get("rates") or {}).get("base") or {}).get("value")
    marked = []
    if old is not None and "base" in rates and rates["base"]["value"] != old:
        from .wpbulk import posts
        value = f"{today:%Y-%m-%d} 기준금리 {old}%→{rates['base']['value']}%"
        for pid, p in posts().items():
            if p.get("status") == "publish" and any(w in (p.get("content") or "") for w in RATE_WORDS):
                wp_cli("post", "meta", "update", pid, LAW_META, value)
                marked.append(pid)
    state["rates"] = {"checked": f"{today:%Y-%m-%d}", **rates}
    parts = [f"기준금리 {rates['base']['value']}%" if "base" in rates else ""]
    parts += [f"{v['name']} {v['value']}%({v['month']})" for k, v in rates.items() if k != "base"]
    msg = "금리(한국은행): " + ", ".join(x for x in parts if x) + (f" / 기준금리 변경 → 재검증 표시 {len(marked)}편" if marked else "")
    add_log(state, True, msg)
    save_state(state)
    return msg


def items(stat: str) -> list[tuple[str, str, str]]:
    """통계표의 항목 (코드, 이름, 주기) — 처음 연결할 때 코드 찾기용."""
    got = _get("StatisticItemList", stat, end=500)
    return [(r.get("ITEM_CODE", ""), r.get("ITEM_NAME", ""), r.get("CYCLE", "")) for r in (got or {}).get("row", [])]


def main(argv: list[str]) -> int:
    if not _key():
        print("한국은행 API: 키 없음")
        return 1
    if argv[1:] == ["--daily"]:
        print(run(), flush=True)
        return 0
    if argv[1:2] == ["items"] and len(argv) > 2:
        for code, name, cycle in items(argv[2]):
            print(f"{code}\t{cycle}\t{name}")
        return 0
    got = base_rate()
    print(f"한국은행 API: {'정상 — 기준금리 ' + str(got[1]) + '% (' + got[0] + ')' if got else '실패'}")
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
