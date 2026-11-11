"""공개 글 구글 색인 기록 (11/11 운영자 '색인 기록 만들어라' — 하루 공개 편수를 늘릴지 판단용).

서치콘솔 'URL 검사' API(무료, 속성당 하루 2,000회)로 공개 글마다 색인됐는지 물어보고:
  - 글별 결과는 keywords.db 의 index_status 표에 (처음 색인 확인한 날 포함)
  - 하루 한 번 state['index'] 에 요약 (/maintenance 'index', tick.py 한 줄)
아직 색인 안 된 글은 매일, 색인된 글은 RECHECK_DAYS 마다 다시 본다 (빠졌는지 확인). 하루 CHECK_PER_DAY 편까지.
실패한 날은 기록하지 않아 다음 수집 때 다시 시도.

판단 기준(참고): 공개 후 WINDOW_DAYS 가 지난 글의 색인률이 GOOD_RATIO 이상이면 '공개 편수 늘려도 됨' 신호.
키 파일·권한은 서치콘솔과 같음 (새 비밀값 없음).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from .search_console import KEY_FILE, SCOPES, SITE_URL

API = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
CHECK_PER_DAY = 200     # API 한도(하루 2,000)보다 훨씬 적게
RECHECK_DAYS = 14       # 색인된 글을 다시 볼 간격
WINDOW_DAYS = 14        # 공개 후 이만큼 지난 글로 색인률을 셈
GOOD_RATIO = 0.8        # 이 이상이면 공개 편수 늘려도 됨
SHOW = 5                # 요약에 남길 미색인 글 수

SCHEMA = """
CREATE TABLE IF NOT EXISTS index_status (
    url TEXT PRIMARY KEY, post_id INTEGER, published TEXT,
    verdict TEXT, coverage TEXT, last_crawl TEXT,
    first_indexed TEXT, checked TEXT
);
"""

# 서치콘솔 coverageState → 쉬운 말
COVERAGE_KO = {
    "Submitted and indexed": "색인됨",
    "Indexed, not submitted in sitemap": "색인됨(사이트맵 밖)",
    "Crawled - currently not indexed": "읽어 갔지만 색인 안 함",
    "Discovered - currently not indexed": "발견만 하고 아직 안 읽음",
    "URL is unknown to Google": "구글이 아직 모름",
}


def available() -> bool:
    return KEY_FILE.exists()


def published_posts(wp=None) -> list[dict]:
    """[{post_id, url, published(YYYY-MM-DD)}]."""
    from .settings import wp_cli
    out = (wp or wp_cli)("post", "list", "--post_type=post", "--post_status=publish",
                         "--fields=ID,url,post_date", "--format=json")
    return [{"post_id": int(p["ID"]), "url": p["url"], "published": str(p["post_date"])[:10]} for p in json.loads(out or "[]")]


def inspect(url: str) -> dict:
    """{verdict, coverage, last_crawl}. verdict: PASS(색인됨) / NEUTRAL / FAIL."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE), scopes=SCOPES)
    resp = AuthorizedSession(creds).post(API, json={"inspectionUrl": url, "siteUrl": SITE_URL}, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"URL 검사 API {resp.status_code}: {resp.text[:200]}")
    r = (resp.json().get("inspectionResult") or {}).get("indexStatusResult") or {}
    return {"verdict": r.get("verdict", ""), "coverage": r.get("coverageState", ""),
            "last_crawl": str(r.get("lastCrawlTime", ""))[:10]}


def is_indexed(row: dict) -> bool:
    return row.get("verdict") == "PASS"


def due(posts: list[dict], known: dict[str, dict], today: date, limit: int = CHECK_PER_DAY) -> list[dict]:
    """오늘 물어볼 글: 처음 보는 글 → 미색인 글(오래 안 본 순) → 색인 후 RECHECK_DAYS 지난 글."""
    cutoff = (today - timedelta(days=RECHECK_DAYS)).isoformat()
    first, waiting, recheck = [], [], []
    for p in posts:
        k = known.get(p["url"])
        if not k:
            first.append(p)
        elif k.get("checked") == today.isoformat():
            continue
        elif not is_indexed(k):
            waiting.append((k.get("checked") or "", p))
        elif (k.get("checked") or "") <= cutoff:
            recheck.append((k.get("checked") or "", p))
    return (first + [p for _, p in sorted(waiting, key=lambda x: x[0])]
            + [p for _, p in sorted(recheck, key=lambda x: x[0])])[:limit]


def summarize(posts: list[dict], known: dict[str, dict], today: date) -> dict:
    rows = [dict(p, **known.get(p["url"], {})) for p in posts]
    checked = [r for r in rows if r.get("checked")]
    indexed = [r for r in checked if is_indexed(r)]
    window_cut = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    old = [r for r in checked if r["published"] <= window_cut]
    old_indexed = [r for r in old if is_indexed(r)]
    days = sorted((date.fromisoformat(r["first_indexed"]) - date.fromisoformat(r["published"])).days
                  for r in indexed if r.get("first_indexed"))
    counts: dict[str, int] = {}
    for r in checked:
        if not is_indexed(r):
            ko = COVERAGE_KO.get(r.get("coverage", ""), r.get("coverage") or "알 수 없음")
            counts[ko] = counts.get(ko, 0) + 1
    waiting = sorted((r for r in checked if not is_indexed(r)), key=lambda r: r["published"])
    ratio = round(len(old_indexed) / len(old), 2) if old else None
    return {
        "date": today.isoformat(),
        "published": len(posts), "checked": len(checked), "indexed": len(indexed),
        "window_days": WINDOW_DAYS, "old": len(old), "old_indexed": len(old_indexed), "old_ratio": ratio,
        # 처음 색인 확인까지 걸린 날 (확인한 날 기준이라 실제보다 늦을 수 있음 — 매일 물어보므로 하루 안팎)
        "median_days": days[len(days) // 2] if days else None,
        "not_indexed": counts,
        "waiting": [{"post_id": r["post_id"], "url": r["url"], "published": r["published"],
                     "coverage": COVERAGE_KO.get(r.get("coverage", ""), r.get("coverage", ""))} for r in waiting[:SHOW]],
        "signal": ("늘려도 됨" if ratio is not None and ratio >= GOOD_RATIO else "유지" if ratio is not None else "아직 판단 못 함"),
    }


def summary_line(s: dict) -> str:
    old = (f" · 공개 {s['window_days']}일 지난 글 {s['old_indexed']}/{s['old']}편({s['old_ratio']:.0%}) → 공개 편수 {s['signal']}"
           if s.get("old") else f" · 공개 {s['window_days']}일 지난 글 없음(아직 판단 못 함)")
    med = f" · 색인까지 보통 {s['median_days']}일" if s.get("median_days") is not None else ""
    why = (" · 미색인: " + ", ".join(f"{k} {v}" for k, v in s["not_indexed"].items())) if s.get("not_indexed") else ""
    return f"색인({s['date']}): 공개 {s['published']}편 중 {s['indexed']}편 색인{old}{med}{why}"


def _load(conn) -> dict[str, dict]:
    conn.executescript(SCHEMA)
    return {r["url"]: dict(r) for r in conn.execute("SELECT * FROM index_status")}


def run(conn, posts: list[dict], today: date, ask=None, limit: int = CHECK_PER_DAY) -> tuple[dict, list[str]]:
    """오늘 몫을 물어보고 저장한 뒤 (요약, 실패 목록). 한 글이 실패해도 나머지는 계속."""
    ask = ask or inspect
    known = _load(conn)
    errors: list[str] = []
    for p in due(posts, known, today, limit):
        try:
            r = ask(p["url"])
        except Exception as e:  # noqa: BLE001 - 권한·한도 문제는 기록하고 다음 날 다시
            errors.append(f"{p['post_id']}: {str(e)[:120]}")
            if len(errors) >= 3:  # 같은 원인(권한 등)이면 계속 물어봐야 소용없음
                break
            continue
        prev = known.get(p["url"], {})
        first = prev.get("first_indexed") or (today.isoformat() if r["verdict"] == "PASS" else "")
        row = {"url": p["url"], "post_id": p["post_id"], "published": p["published"], **r,
               "first_indexed": first, "checked": today.isoformat()}
        conn.execute("INSERT OR REPLACE INTO index_status VALUES (:url,:post_id,:published,:verdict,:coverage,"
                     ":last_crawl,:first_indexed,:checked)", row)
        known[p["url"]] = row
    conn.commit()
    return summarize(posts, known, today), errors


def record(state: dict, today: str | None = None) -> str:
    """하루 한 번 state['index'] 에 요약 (새벽 수집이 부름). 실패하면 기록하지 않아 다음 수집 때 다시."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    if (state.get("index") or {}).get("date") == today:
        return ""
    if not available():
        return "색인: 서치콘솔 키 없음"
    from .keyword_db import connect
    posts = published_posts()
    with connect() as conn:
        s, errors = run(conn, posts, date.fromisoformat(today))
    if errors and not s["checked"]:
        return f"색인 확인 실패: {errors[0]} — 서치콘솔 속성에 서비스 계정 사용자 권한이 있는지 확인"
    state["index"] = s
    return summary_line(s) + (f" (확인 실패 {len(errors)}편: {errors[0]})" if errors else "")


if __name__ == "__main__":
    st: dict = {}
    print(record(st) or "오늘 이미 기록함")
