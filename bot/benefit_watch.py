"""새로 생긴 혜택 감시 (하루 1번, 키워드 수집 끝) — 정부24 공공서비스 API 에 새로 등록된 전국·광역 혜택을 글감으로.

왜: 새 지원사업·정책은 아직 검색량이 없어 키워드 후보에 안 잡힌다 → 사람들이 찾기 시작하기 전에 먼저 써 두면 그때 이미 자리를 잡고 있다
(11/5 운영자 '새로 만들어지는 혜택·정책은 검색량이 없는데 그냥 글 적나'). 법령 개정(law_watch)과 같은 방식:
  state["new_benefits"] → 읽기 API(/next) 의 new_benefits → 글쓰기 세션이 law_changes 다음으로 글감 → 큐 JSON 의 "new_benefit" 키로 다시 안 줌.
시군구 혜택은 한 지역 한정이라 뺀다 (지역을 전국처럼 쓰는 오류 유형도 피함). 광역시도는 지역을 밝혀 쓴다.
"""
from __future__ import annotations

import datetime as dt
import re

from .benefit_api import fetch

DAYS = 180                    # 최근 이만큼 안에 등록된 혜택 (전국 단위 새 혜택은 몇 달에 몇 개라 넉넉히)
KEEP = 20
AGENCY_TYPES = ("중앙행정기관", "공공기관", "광역시도")   # 시군구·지방출자기관은 한 지역 한정이라 뺌
USERS = ("개인", "가구", "소상공인")                      # 법인·시설·단체 대상은 뺌


def apply_closed(period: str, today: dt.date) -> bool:
    """신청기한 '2026.10.12~2026.10.30' 의 끝이 오늘보다 앞이면 True (상시·기관별 상이 등은 False)."""
    dates = re.findall(r"(20\d\d)\.\s*(\d{1,2})\.\s*(\d{1,2})", period or "")
    if not dates:
        return False
    y, m, d = map(int, dates[-1])
    try:
        return dt.date(y, m, d) < today
    except ValueError:
        return False


def pick(rows: list[dict], done: set[str], today: dt.date | None = None) -> list[dict]:
    """새 혜택 중 글감으로 줄 것 (전국·광역, 개인·가구·소상공인 대상, 신청 기간이 안 끝난 것, 아직 안 쓴 것), 새로 등록된 순."""
    today = today or dt.date.today()
    out = []
    for r in rows:
        if (r.get("소관기관유형") not in AGENCY_TYPES or not any(u in (r.get("사용자구분") or "") for u in USERS)
                or str(r.get("서비스ID")) in done or apply_closed(r.get("신청기한", ""), today)):
            continue  # 신청이 이미 끝난 혜택은 글로 써도 쓸모없음 (11/5 첫 시험에서 10월 마감 건이 섞임)
        out.append({
            "key": str(r.get("서비스ID")), "name": r.get("서비스명", ""), "agency": r.get("소관기관명", ""),
            "scope": "전국" if r.get("소관기관유형") != "광역시도" else f"{r.get('소관기관명', '')} 한정",
            "summary": (r.get("서비스목적요약") or "")[:200], "target": (r.get("지원대상") or "")[:300],
            "apply": r.get("신청기한", ""), "url": r.get("상세조회URL", ""), "registered": str(r.get("등록일시", ""))[:8],
        })
    return sorted(out, key=lambda x: (x["scope"] == "전국", x["registered"]), reverse=True)[:KEEP]  # 전국 먼저, 그 안에서 최근 등록 순


def run(today: dt.date | None = None) -> str:
    from .state import add_log, load_state, save_state
    today = today or dt.date.today()
    got = fetch("serviceList", page=1, perPage=500, **{"cond[등록일시::GTE]": f"{today - dt.timedelta(days=DAYS):%Y%m%d}"})
    if got is None:
        return "새 혜택 감시: 공공서비스 API 못 받음 → 건너뜀"
    state = load_state()
    items = pick(got.get("data") or [], set(state.get("new_benefits_done") or []), today)
    state["new_benefits"] = items
    msg = (f"새 혜택 감시: 최근 {DAYS}일 등록 {got.get('matchCount', 0)}개 중 글감 {len(items)}개 (전국·광역, 개인·가구·소상공인)"
           + (f" — {', '.join(i['name'] for i in items[:3])}" if items else ""))
    add_log(state, True, msg)
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
