"""GA4 방문 데이터 → 봇 기록 (11/9 운영자 '체류시간 같은 것도 참고해서 더 나은 방향으로').

서치콘솔은 검색 결과에서 클릭하기 전까지만 보여 준다. GA4 는 들어온 뒤를 본다:
  - 글별 방문(세션)·참여율·평균 참여 시간
  - 사이트 안 이동: 어느 글에서 어느 글로 넘어갔는지 (페이지 리퍼러가 우리 사이트인 페이지뷰)
하루 한 번 state['ga4'] 에 요약을 남긴다 (/maintenance 'ga4', tick.py 한 줄). 판단에 쓸 신호(signals)는
방문이 MIN_SESSIONS 이상 쌓인 글만 — 방문 몇 번으로 '안 읽힌다'고 판단하면 오히려 틀린다.

준비물 (운영자, 한 번만):
  1) GA4 속성 액세스 관리에 서치콘솔용 서비스 계정 이메일을 '뷰어'로 추가 (이메일은 실패 기록에 나옴)
  2) 그 서비스 계정의 Google Cloud 프로젝트에서 'Google Analytics Data API' 사용 설정
키 파일은 서치콘솔과 같은 것 (/etc/first-lab/gsc-service-account.json) — 새 비밀값 없음.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime

from .search_console import KEY_FILE

PROPERTY_ID = "563317951"   # 퍼스트랩 GA4 속성 (11/9 운영자) — 측정 ID G-SHDKPTJPJR 의 속성
SITE_HOST = "first-lab.kr"
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
DAYS = 28
MIN_SESSIONS = 30            # 이만큼 방문이 쌓인 글만 신호로 씀
LOW_ENGAGEMENT = 0.35        # 참여율이 이보다 낮으면 '들어와서 바로 나감' 신호
HIGH_ENGAGEMENT = 0.65       # 이보다 높으면 '잘 읽힘' 신호
TOP = 5


def available() -> bool:
    return KEY_FILE.exists()


def service_email() -> str:
    try:
        return json.loads(KEY_FILE.read_text(encoding="utf-8")).get("client_email", "")
    except (OSError, ValueError):
        return ""


def _report(body: dict) -> list[dict]:
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE), scopes=SCOPES)
    resp = AuthorizedSession(creds).post(
        f"https://analyticsdata.googleapis.com/v1beta/properties/{PROPERTY_ID}:runReport", json=body, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"GA4 API {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    dims = [d["name"] for d in data.get("dimensionHeaders", [])]
    mets = [m["name"] for m in data.get("metricHeaders", [])]
    rows = []
    for r in data.get("rows", []):
        row = {n: v["value"] for n, v in zip(dims, r.get("dimensionValues", []))}
        row.update({n: float(v["value"]) for n, v in zip(mets, r.get("metricValues", []))})
        rows.append(row)
    return rows


def fetch() -> tuple[list[dict], list[dict]]:
    """(글별 성과, 사이트 안 이동)."""
    span = [{"startDate": f"{DAYS}daysAgo", "endDate": "yesterday"}]
    pages = _report({"dateRanges": span, "dimensions": [{"name": "pagePath"}],
                     "metrics": [{"name": "sessions"}, {"name": "engagementRate"},
                                 {"name": "userEngagementDuration"}, {"name": "screenPageViews"}],
                     "limit": 1000})
    moves = _report({"dateRanges": span, "dimensions": [{"name": "pageReferrer"}, {"name": "pagePath"}],
                     "metrics": [{"name": "screenPageViews"}], "limit": 2000})
    return pages, moves


def _path(url_or_path: str) -> str:
    p = urllib.parse.urlparse(url_or_path).path if "://" in url_or_path else url_or_path
    return urllib.parse.unquote(p or "/")


def summarize(pages: list[dict], moves: list[dict], today: str) -> dict:
    sessions = sum(p.get("sessions", 0) for p in pages)
    seconds = sum(p.get("userEngagementDuration", 0) for p in pages)
    internal = []
    for m in moves:
        ref = m.get("pageReferrer", "")
        if SITE_HOST in ref and _path(ref) != _path(m.get("pagePath", "")):
            internal.append({"from": _path(ref), "to": _path(m.get("pagePath", "")), "views": int(m.get("screenPageViews", 0))})
    internal.sort(key=lambda x: -x["views"])
    rows = [{"page": _path(p.get("pagePath", "")), "sessions": int(p.get("sessions", 0)),
             "engagement": round(p.get("engagementRate", 0), 2),
             "avg_seconds": round(p.get("userEngagementDuration", 0) / p["sessions"]) if p.get("sessions") else 0}
            for p in pages]
    rows.sort(key=lambda r: -r["sessions"])
    enough = [r for r in rows if r["sessions"] >= MIN_SESSIONS]
    return {
        "date": today, "days": DAYS,
        "sessions": int(sessions),
        "avg_seconds": round(seconds / sessions) if sessions else 0,
        "internal_moves": sum(x["views"] for x in internal),
        "top": rows[:TOP],
        "top_moves": internal[:TOP],
        # 판단 신호: 방문이 충분히 쌓인 글만
        "low_engagement": [r["page"] for r in enough if r["engagement"] < LOW_ENGAGEMENT],
        "high_engagement": [r["page"] for r in enough if r["engagement"] >= HIGH_ENGAGEMENT],
    }


def summary_line(s: dict) -> str:
    return (f"방문(GA4 {s['days']}일, {s['date']} 기록): 세션 {s['sessions']:,}·평균 참여 {s['avg_seconds']}초·"
            f"사이트 안 이동 {s['internal_moves']:,}회 · 바로 나가는 글 {len(s['low_engagement'])}·잘 읽히는 글 {len(s['high_engagement'])}"
            f" (방문 {MIN_SESSIONS}회 이상 글만 판단)")


def record(state: dict, today: str | None = None) -> str:
    """하루 한 번 state['ga4'] 에 요약. 권한이 없으면 무엇을 해야 하는지 남김 (같은 날 다시 안 부름)."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    prev = state.get("ga4") or {}
    if prev.get("date") == today:
        return ""
    if not available():
        return "GA4: 서비스 계정 키 없음"
    try:
        pages, moves = fetch()
    except Exception as e:  # noqa: BLE001 - 권한·API 미사용 설정 등은 운영자 안내로
        state["ga4"] = {"date": today, "error": str(e)[:200]}
        return (f"GA4 읽기 실패: {str(e)[:150]} — GA4 속성 액세스 관리에 '{service_email()}' 를 뷰어로 추가하고, "
                "그 계정의 Google Cloud 프로젝트에서 Google Analytics Data API 를 사용 설정")
    state["ga4"] = summarize(pages, moves, today)
    return summary_line(state["ga4"])
