"""구글 서치콘솔 성과 → 키워드 점수 자동 조정 (무료, 공식 API).

매일 최근 28일 '검색어별 노출·클릭·평균 순위'를 가져와서:
  1) 기회 키워드: 노출은 되는데(20회+) 순위가 8~30위인 검색어 중 아직 전용 글이 없는 것
     → 후보로 추가하고 크게 가산 (구글이 이미 우리 사이트와 연관 있다고 본 검색어)
  2) 잘 되는 주제: 클릭이 나거나 순위 15위 안인 글의 '원 키워드(헤드)' → 같은 주제 롱테일 가산
  3) 안 되는 주제: 발행 21일이 지나도 노출이 거의 없는 글의 헤드 → 같은 주제 감점

준비물: 서비스 계정 JSON 키 (/etc/first-lab/gsc-service-account.json) + 서치콘솔에 그 계정을 사용자로 추가
점검: python -m bot.search_console
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from .keyword_db import norm

KEY_FILE = Path("/etc/first-lab/gsc-service-account.json")
SITE_URL = "https://first-lab.kr/"  # 서치콘솔 'URL 접두어' 속성
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

OPPORTUNITY_MIN_IMPRESSIONS = 20
OPPORTUNITY_POSITION = (8, 30)
GOOD_POSITION = 15
STALE_DAYS = 21          # 발행 후 이 기간이 지나도 노출이 없으면 '안 되는 주제'
STALE_MAX_IMPRESSIONS = 5
POPULAR_MIN_CLICKS = 5   # 홈 대표 글로 올릴 최소 클릭 (28일)
POPULAR_OPTION = "firstlab_popular_url"  # wordpress/mu-plugins/first-lab-home.php 가 읽음

SCHEMA = """
CREATE TABLE IF NOT EXISTS gsc_queries (   -- 최근 28일 검색어 성과 (매일 덮어씀)
    query TEXT, page TEXT, clicks INTEGER, impressions INTEGER, position REAL
);
CREATE TABLE IF NOT EXISTS gsc_page_history (  -- 글 주소별 최근 28일 성과를 매일 기록 (하락 감지용)
    date TEXT, page TEXT, clicks INTEGER, impressions INTEGER, position REAL,
    PRIMARY KEY (date, page)
);
CREATE TABLE IF NOT EXISTS gsc_signals (   -- 점수 조정 신호
    norm   TEXT PRIMARY KEY,               -- 키워드 또는 헤드 (공백 제거)
    kind   TEXT NOT NULL,                  -- opportunity | good_head | bad_head
    detail TEXT
);
"""


def available() -> bool:
    return KEY_FILE.exists()


def check_key_file() -> None:
    """키 파일이 '서비스 계정 JSON 전체'인지 확인 (키 ID 만 붙여넣는 실수 방지)."""
    try:
        data = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SystemExit(f"{KEY_FILE} 내용이 JSON 파일 전체가 아닙니다. "
                         "다운로드한 .json 파일의 { 부터 } 까지 전부 넣어야 합니다.") from None
    missing = [k for k in ("type", "client_email", "private_key") if k not in data]
    if missing:
        raise SystemExit(f"서비스 계정 키 파일이 아닙니다 (없는 항목: {', '.join(missing)})")


def _query(dimensions: list[str], days: int, row_limit: int) -> list[dict]:
    """서치콘솔 성과 조회 → [{keys, clicks, impressions, position}]. (데이터는 2~3일 늦게 들어옴)"""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE), scopes=SCOPES)
    session = AuthorizedSession(creds)
    end = datetime.now().date() - timedelta(days=2)
    body = {"startDate": str(end - timedelta(days=days)), "endDate": str(end),
            "dimensions": dimensions, "rowLimit": row_limit}
    url = ("https://www.googleapis.com/webmasters/v3/sites/"
           f"{urllib.parse.quote(SITE_URL, safe='')}/searchAnalytics/query")
    resp = session.post(url, json=body, timeout=60)
    resp.raise_for_status()
    return [{"keys": r["keys"], "clicks": int(r["clicks"]), "impressions": int(r["impressions"]),
             "position": float(r["position"])} for r in resp.json().get("rows", [])]


def fetch(days: int = 28) -> list[dict]:
    """검색어·페이지별 성과 (키워드 점수 조정용). 드물게 검색된 검색어는 구글이 빼고 줌 → 합계는 fetch_totals."""
    return [{"query": r["keys"][0], "page": r["keys"][1], "clicks": r["clicks"],
             "impressions": r["impressions"], "position": r["position"]}
            for r in _query(["query", "page"], days, 5000)]


# ── 검색 성과 요약 (11/8 운영자 '두 개 다 해 봐': 남의 사이트 비교 말고 우리 실제 성과로 판단) ──
# 검색어를 빼고 날짜·페이지로만 물어야 익명 검색어까지 든 진짜 합계가 나온다 (검색어·페이지 조회는 7일 노출 1회로 보였음).
SUMMARY_DAYS = 28
TOP_PAGES = 5


def fetch_totals(days: int = SUMMARY_DAYS) -> tuple[list[dict], list[dict]]:
    """(날짜별, 글 주소별) 성과."""
    daily = [{"date": r["keys"][0], **{k: r[k] for k in ("clicks", "impressions", "position")}}
             for r in _query(["date"], days, 1000)]
    pages = [{"page": r["keys"][0], **{k: r[k] for k in ("clicks", "impressions", "position")}}
             for r in _query(["page"], days, 5000)]
    return daily, pages


def _weighted_position(rows: list[dict]) -> float | None:
    imp = sum(r["impressions"] for r in rows)
    return round(sum(r["position"] * r["impressions"] for r in rows) / imp, 1) if imp else None


def summarize(daily: list[dict], pages: list[dict], published: int | None, today: str) -> dict:
    """봇 기록(/maintenance 'search')에 남길 요약. 최근 7일과 그 전 7일을 같이 둬 늘고 주는지 보이게."""
    daily = sorted(daily, key=lambda r: r["date"])
    last7, prev7 = daily[-7:], daily[-14:-7]
    top = sorted(pages, key=lambda r: (-r["clicks"], -r["impressions"]))[:TOP_PAGES]
    return {
        "date": today,
        "days": SUMMARY_DAYS,
        "clicks": sum(r["clicks"] for r in daily),
        "impressions": sum(r["impressions"] for r in daily),
        "position": _weighted_position(daily),
        "last7": {"clicks": sum(r["clicks"] for r in last7), "impressions": sum(r["impressions"] for r in last7)},
        "prev7": {"clicks": sum(r["clicks"] for r in prev7), "impressions": sum(r["impressions"] for r in prev7)},
        "pages_seen": sum(1 for r in pages if r["impressions"] > 0),
        "published": published,
        "top": [{"page": urllib.parse.unquote(urllib.parse.urlparse(r["page"]).path), "clicks": r["clicks"],
                 "impressions": r["impressions"], "position": round(r["position"], 1)} for r in top],
    }


def summary_line(s: dict) -> str:
    """사람이 읽는 한 줄 (tick.py·봇 기록)."""
    pos = f"{s['position']}위" if s.get("position") else "-"
    pub = f"/공개 {s['published']}편" if s.get("published") is not None else ""
    return (f"검색({s['days']}일, {s['date']} 기록): 노출 {s['impressions']:,}·클릭 {s['clicks']:,}·평균 {pos} · "
            f"최근 7일 노출 {s['last7']['impressions']:,}(그 전 7일 {s['prev7']['impressions']:,}) · "
            f"노출된 글 {s['pages_seen']}{pub}")


def _published_count() -> int | None:
    try:
        from .settings import wp_cli
        return int(wp_cli("post", "list", "--post_type=post", "--post_status=publish", "--format=count").strip())
    except Exception:  # noqa: BLE001 - 글 수를 못 세도 성과 기록은 남김
        return None


def record(state: dict, today: str | None = None) -> str:
    """하루 한 번 state['search'] 에 요약을 남김 (매일 수집·배포 점검이 부름 — 이미 오늘 것이 있으면 건너뜀)."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    if (state.get("search") or {}).get("date") == today:
        return ""
    if not available():
        return "서치콘솔: 미연동"
    daily, pages = fetch_totals()
    state["search"] = summarize(daily, pages, _published_count(), today)
    try:  # 어떤 검색어로 노출됐는지 (11/8 운영자 '5위라고? 난 안 보이던데' — 평균 순위만으로는 어떤 검색어인지 모름)
        state["search"]["queries"] = top_queries(fetch(SUMMARY_DAYS))
    except Exception:  # noqa: BLE001 - 검색어 목록이 없어도 합계 기록은 남김
        pass
    return summary_line(state["search"])


def top_queries(rows: list[dict], limit: int = TOP_PAGES) -> list[dict]:
    """노출 많은 검색어 (드물게 검색된 검색어는 구글이 빼고 줌 — 합계와 안 맞을 수 있음)."""
    top = sorted(rows, key=lambda r: (-r["impressions"], -r["clicks"]))[:limit]
    return [{"query": r["query"], "page": urllib.parse.unquote(urllib.parse.urlparse(r["page"]).path),
             "impressions": r["impressions"], "clicks": r["clicks"], "position": round(r["position"], 1)} for r in top]


def update_signals(conn, rows: list[dict]) -> dict:
    """성과 데이터 저장 + 점수 조정 신호 계산."""
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM gsc_queries")
    conn.executemany("INSERT INTO gsc_queries VALUES (:query,:page,:clicks,:impressions,:position)", rows)
    conn.execute("DELETE FROM gsc_signals")

    used = {r["norm"]: r["reason"] for r in conn.execute("SELECT norm, reason FROM keywords WHERE status='used'")}
    volumes = {r["norm"]: r["total"] for r in conn.execute("SELECT norm, total FROM keywords")}
    from .scoring import find_head

    def head_of(n: str) -> str:
        return find_head(n, volumes.get(n, 1), volumes) or n

    signals: dict[str, tuple[str, str]] = {}
    # 1) 기회 키워드
    for r in rows:
        n = norm(r["query"])
        lo, hi = OPPORTUNITY_POSITION
        if r["impressions"] >= OPPORTUNITY_MIN_IMPRESSIONS and lo <= r["position"] <= hi and n not in used:
            signals[n] = ("opportunity", f"노출 {r['impressions']}·{r['position']:.0f}위")
            if n not in volumes:  # 네이버 창고에 없던 검색어도 후보로 추가
                conn.execute(
                    "INSERT OR IGNORE INTO keywords (keyword, norm, total, status, first_seen, last_checked)"
                    " VALUES (?,?,?,?,?,?)",
                    (r["query"], n, r["impressions"], "new", *(datetime.now().strftime("%Y-%m-%d"),) * 2),
                )
    # 2) 잘 되는 주제 / 3) 안 되는 주제 (우리 글 기준)
    by_page: dict[str, dict] = {}
    for r in rows:
        p = by_page.setdefault(r["page"], {"clicks": 0, "impressions": 0, "best": 999.0, "pos_sum": 0.0})
        p["clicks"] += r["clicks"]
        p["impressions"] += r["impressions"]
        p["best"] = min(p["best"], r["position"])
        p["pos_sum"] += r["position"] * r["impressions"]  # 노출 가중 평균 순위 계산용
    today = datetime.now().strftime("%Y-%m-%d")
    conn.executemany(
        "INSERT OR REPLACE INTO gsc_page_history VALUES (?,?,?,?,?)",
        [(today, u, p["clicks"], p["impressions"], round(p["pos_sum"] / max(p["impressions"], 1), 1))
         for u, p in by_page.items()],
    )
    performing_pages = [u for u, p in by_page.items() if p["clicks"] > 0 or p["best"] <= GOOD_POSITION]
    stale_cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
    for r in conn.execute("SELECT norm, first_seen FROM keywords WHERE status='used'"):
        h = head_of(r["norm"])
        # 글 주소에 키워드가 들어가므로(고유주소 /%postname%/) 주소로 매칭
        page_hit = [u for u in by_page if r["norm"] in norm(urllib.parse.unquote(u))]
        if any(u in performing_pages for u in page_hit):
            signals.setdefault(h, ("good_head", "같은 주제 글 성과 있음"))
        elif r["first_seen"] <= stale_cutoff and sum(by_page[u]["impressions"] for u in page_hit) <= STALE_MAX_IMPRESSIONS:
            signals.setdefault(h, ("bad_head", f"{STALE_DAYS}일 지나도 노출 거의 없음"))
    conn.executemany("INSERT OR REPLACE INTO gsc_signals VALUES (?,?,?)",
                     [(n, k, d) for n, (k, d) in signals.items()])
    conn.commit()
    kinds = [k for k, _ in signals.values()]
    return {"rows": len(rows), "opportunity": kinds.count("opportunity"),
            "good_head": kinds.count("good_head"), "bad_head": kinds.count("bad_head")}


def top_page(rows: list[dict], min_clicks: int = POPULAR_MIN_CLICKS) -> str:
    """최근 28일 클릭이 가장 많은 글 주소 (min_clicks 미만이면 빈 문자열)."""
    clicks: dict[str, int] = {}
    for r in rows:
        clicks[r["page"]] = clicks.get(r["page"], 0) + r["clicks"]
    best = max(clicks, key=clicks.get, default="")
    return best if best and clicks[best] >= min_clicks else ""


def save_popular(rows: list[dict]) -> str:
    """홈 대표 글 후보를 워드프레스 옵션에 저장 (없으면 지워서 최신 글이 대표 글이 되게)."""
    import subprocess

    from .settings import wp_cli

    url = top_page(rows)
    if url:
        wp_cli("option", "update", POPULAR_OPTION, url)
    else:
        try:
            wp_cli("option", "delete", POPULAR_OPTION)
        except subprocess.CalledProcessError:
            pass  # 원래 없음
    return url


def load_signals(conn) -> dict[str, str]:
    conn.executescript(SCHEMA)
    return {r["norm"]: r["kind"] for r in conn.execute("SELECT norm, kind FROM gsc_signals")}


if __name__ == "__main__":
    from .keyword_db import connect

    if not available():
        raise SystemExit(f"서비스 계정 키가 없습니다: {KEY_FILE}")
    check_key_file()
    data = fetch()
    with connect() as conn:
        print(update_signals(conn, data))
    print("상위 검색어:", sorted(data, key=lambda r: -r["impressions"])[:10])
