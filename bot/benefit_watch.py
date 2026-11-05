"""새로 생긴 혜택 감시 (하루 1번, 키워드 수집 끝) — 정부24 공공서비스 API 에 새로 등록된 전국·광역 혜택을 글감으로.

왜: 새 지원사업·정책은 아직 검색량이 없어 키워드 후보에 안 잡힌다 → 사람들이 찾기 시작하기 전에 먼저 써 두면 그때 이미 자리를 잡고 있다
(11/5 운영자 '새로 만들어지는 혜택·정책은 검색량이 없는데 그냥 글 적나'). 법령 개정(law_watch)과 같은 방식:
  state["new_benefits"] → 읽기 API(/next) 의 new_benefits → 글쓰기 세션이 law_changes 다음으로 글감 → 큐 JSON 의 "new_benefit" 키로 다시 안 줌.
시군구 혜택은 한 지역 한정이라 뺀다 (지역을 전국처럼 쓰는 오류 유형도 피함). 광역시도는 지역을 밝혀 쓴다.

내용이 바뀐 혜택 (11/5 운영자 '모든 글 업데이트 안 하나'): 우리 글(공개·예약·초안)에 이름이 나오는 혜택의
지원 내용·대상·기준·신청 기한·방법을 매일 저장본과 비교 → 바뀌면 그 글에 표시(bot/changed.py) → 공개 글은 재검증, 초안은 공개 직전 보류.
정부24 수정일시는 글자 하나만 고쳐도 바뀌어서 쓰지 않고, 내용 자체를 비교한다. 처음 본 혜택은 저장만 (비교할 이전 내용이 없음).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from .benefit_api import fetch

DAYS = 180                    # 최근 이만큼 안에 등록된 혜택 (전국 단위 새 혜택은 몇 달에 몇 개라 넉넉히)
KEEP = 20
AGENCY_TYPES = ("중앙행정기관", "공공기관", "광역시도")   # 시군구·지방출자기관은 한 지역 한정이라 뺌
USERS = ("개인", "가구", "소상공인")                      # 법인·시설·단체 대상은 뺌
SNAPSHOT = Path("/var/lib/first-lab/bot/benefit-snapshot.json")  # 서비스ID → 내용 지문 (우리 글에 나오는 혜택만)
CONTENT_FIELDS = ("지원내용", "지원대상", "선정기준", "신청기한", "신청방법", "지원유형", "서비스목적요약")
MIN_NAME = 4        # 이보다 짧은 이름은 다른 말 속에 우연히 들어 있을 수 있어 비교 안 함
PAGE = 1000


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


def fingerprint(row: dict) -> str:
    text = "\n".join(re.sub(r"\s+", " ", str(row.get(f) or "")).strip() for f in CONTENT_FIELDS)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def all_services(fetch_fn=None) -> list[dict] | None:
    """전국·광역 혜택 전부 (유형마다 1000개씩). 하나라도 못 받으면 None (반쪽 비교로 잘못 표시하지 않게)."""
    fetch_fn = fetch_fn or fetch
    rows = []
    for kind in AGENCY_TYPES:
        page = 1
        while True:
            got = fetch_fn("serviceList", page=page, perPage=PAGE, **{"cond[소관기관유형::EQ]": kind})
            if got is None:
                return None
            data = got.get("data") or []
            rows += data
            if len(data) < PAGE:
                break
            page += 1
    return rows


def changed_benefits(rows: list[dict], texts: dict[str, str], before: dict[str, str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """(글 번호 → 내용이 바뀐 혜택 이름들, 새 저장본). texts: 글 번호 → 제목+본문, before: 서비스ID → 지문."""
    hits: dict[str, list[str]] = {}
    snapshot: dict[str, str] = {}
    for r in rows:
        name, sid = str(r.get("서비스명") or "").strip(), str(r.get("서비스ID") or "")
        if len(name) < MIN_NAME or not sid:
            continue
        posts = [pid for pid, text in texts.items() if name in text]
        if not posts:
            continue
        snapshot[sid] = fingerprint(r)
        if sid in before and before[sid] != snapshot[sid]:
            for pid in posts:
                hits.setdefault(pid, []).append(name)
    return hits, snapshot


def watch_changes(today: dt.date, fetch_fn=None, bulk=None, wp=None, snapshot_file: Path = SNAPSHOT) -> str:
    from . import changed, wpbulk
    rows = all_services(fetch_fn)
    if rows is None:
        return "혜택 내용 비교: API 못 받음 → 건너뜀"
    posts = bulk if bulk is not None else wpbulk.posts()
    texts = {pid: f"{p.get('title', '')}\n{p.get('content', '')}" for pid, p in posts.items() if p.get("status") in changed.LIVE}
    try:
        before = json.loads(snapshot_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        before = {}
    hits, snapshot = changed_benefits(rows, texts, before)
    marked = []
    for pid, names in hits.items():
        marked += changed.flag([pid], f"{today:%Y-%m-%d} 혜택 내용 변경(정부24): {', '.join(sorted(set(names)))[:120]}", wp)
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot_file.write_text(json.dumps({**before, **snapshot}, ensure_ascii=False), encoding="utf-8")
    new = len(set(snapshot) - set(before))
    return f"혜택 내용 비교: 우리 글에 나오는 혜택 {len(snapshot)}개 (처음 저장 {new}) / 내용 바뀜 → 재확인 표시 {len(marked)}편"


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
    try:
        msg += " / " + watch_changes(today)
    except Exception as e:  # noqa: BLE001 - 비교가 실패해도 새 혜택 글감은 저장
        msg += f" / 혜택 내용 비교 실패: {e}"
    add_log(state, True, msg)
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
