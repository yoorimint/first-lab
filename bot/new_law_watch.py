"""새 법 감시 (하루 1번, 키워드 수집 끝) — 아직 우리 글이 다루지 않은 분야에서 곧 시행되는 법령을 글감으로.

왜: 법령 감시(law_watch)는 우리 글이 근거로 쓴 법만 보므로 처음 생기는 수당·공제·과태료는 못 잡는다 (11/9 운영자 '그것도 해라').
법제처 API 로 '2~8주 뒤 시행' 법령 전체를 읽고, 생활 부처의 법률·시행령 제정·개정 중 개정 이유에 개인 지원·의무 말이 있는 것만 고른다.
흐름은 다른 글감과 같다: state["new_laws"] → 읽기 API(/next) new_laws → 글쓰기 세션(new_policies 다음) → 큐 JSON "new_law": key,
쓸 만하지 않으면 skip 파일 "new_laws" (다시 안 나옴). 이미 우리 글이 근거로 쓴 법은 law_watch 몫이라 뺀다.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse
import xml.etree.ElementTree as ET

from . import law_api

FROM_DAYS, TO_DAYS = 14, 56   # 시행 2~8주 전에 써 두면 시행·신청이 시작될 때 자리를 잡고 있음
KEEP = 10
MIN_SCORE = 2
MAX_DETAILS = 80              # 개정 이유를 열어 보는 최대 법령 수 (하루 한 번, 법제처 호출 수)
KINDS = ("제정", "일부개정", "전부개정")   # 타법개정(다른 법 따라 이름만 바뀜)은 뺌
LEVELS = ("법률", "대통령령")
LIFE = ("고용노동부", "국토교통부", "보건복지부", "기획재정부", "재정경제부", "행정안전부", "국세청", "금융위원회", "여성가족부",
        "성평등가족부", "교육부", "국가보훈부", "중소벤처기업부", "공정거래위원회", "환경부", "기후에너지환경부", "질병관리청",
        "식품의약품안전처", "농림축산식품부", "해양수산부", "과학기술정보통신부", "산업통상부", "산업통상자원부", "문화체육관광부")
# 개정 이유에 이런 말이 있으면 개인에게 닿는 개정 (지원·혜택·의무·제재)
PERSONAL = re.compile(r"(지원|수당|급여|공제|감면|면제|환급|장려금|바우처|보조|대출|혜택|신청|지급|과태료|벌금|의무|신고|금지|허용|"
                      r"근로자|청년|노인|어르신|아동|임산부|장애인|소상공인|임차인|세입자|운전자|국민)")
INTERNAL = re.compile(r"(용어|자구|조문 정비|위임 근거|인용 조문|명확히|정비하려는|알기 쉽게|법체계)")
# 기관·위원회 운영 이야기 (개인 글감 아님 — 점수를 깎음)
INSTITUTION = re.compile(r"(위원회|위원 정수|실태조사|명칭|설치 근거|설립|공단의|기본계획|연구윤리|이전기관|지원체계)")


def _rows(xml: str) -> list[dict]:
    out = []
    for law in ET.fromstring(xml).iter("law"):
        out.append({"name": (law.findtext("법령명한글") or "").strip(), "mst": law.findtext("법령일련번호") or "",
                    "date": law.findtext("시행일자") or "", "kind": (law.findtext("제개정구분명") or "").strip(),
                    "level": (law.findtext("법령구분명") or "").strip(), "agency": (law.findtext("소관부처명") or "").strip()})
    return out


def upcoming(today: dt.date, fetch=law_api.fetch) -> list[dict]:
    span = f"{today + dt.timedelta(days=FROM_DAYS):%Y%m%d}~{today + dt.timedelta(days=TO_DAYS):%Y%m%d}"
    rows: list[dict] = []
    for page in range(1, 11):
        xml = fetch("search", "eflaw", efYd=span, display="100", page=str(page))
        if not xml:
            break
        got = _rows(xml)
        rows += got
        if len(got) < 100:
            break
    return rows


def candidates(rows: list[dict], covered: set[str], done: set[str]) -> list[dict]:
    """생활 부처의 법률·시행령 제정·개정 중 아직 안 다룬 것 (같은 법은 시행일 가까운 판 하나)."""
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["date"]):
        key = f"{r['name']}|{r['date']}"
        if (r["kind"] not in KINDS or r["level"] not in LEVELS or not any(a in r["agency"] for a in LIFE)
                or r["name"].replace(" ", "") in covered or key in done or r["name"] in seen):
            continue
        seen.add(r["name"])
        out.append(dict(r, key=key))
    return out


def reason_of(row: dict, fetch=law_api.fetch) -> str:
    xml = fetch("service", "eflaw", MST=row["mst"], efYd=row["date"])
    if not xml:
        return ""
    node = ET.fromstring(xml).find("제개정이유/제개정이유내용")
    text = re.sub(r"\s+", " ", (node.text or "") if node is not None else "")
    return text.replace("<법제처 제공>", "").replace("[일부개정]", "").replace("[제정]", "").replace("◇ 개정이유 및 주요내용", "").strip()


def score(row: dict, reason: str) -> int:
    if not reason or (INTERNAL.search(reason) and len(set(PERSONAL.findall(reason))) < 2):
        return 0
    return len(set(PERSONAL.findall(reason))) + (1 if row["kind"] == "제정" else 0) - len(set(INSTITUTION.findall(reason)))


def _covered() -> set[str]:
    """우리 글이 이미 근거로 쓴 법 이름 (law_watch 가 개정을 따로 넘김)."""
    from .law_watch import law_name, watched_laws
    return {law_name(base).replace(" ", "") for base in watched_laws()}


def run(today: dt.date | None = None, fetch=law_api.fetch, covered: set[str] | None = None) -> str:
    from .state import add_log, load_state, save_state
    today = today or dt.date.today()
    if not law_api.available():
        return "새 법 감시: 법제처 API 키 없음 → 건너뜀"
    rows = upcoming(today, fetch)
    state = load_state()
    cands = candidates(rows, _covered() if covered is None else covered, set(state.get("new_laws_done") or []))
    picked = []
    for row in cands[:MAX_DETAILS]:
        reason = reason_of(row, fetch)
        s = score(row, reason)
        if s >= MIN_SCORE:
            picked.append({"key": row["key"], "name": row["name"], "date": f"{row['date'][:4]}-{row['date'][4:6]}-{row['date'][6:]}",
                           "kind": row["kind"], "agency": row["agency"], "reason": reason[:400], "score": s,
                           "url": "https://www.law.go.kr/법령/" + urllib.parse.quote(row["name"].replace(" ", ""))})
    picked.sort(key=lambda p: (-p["score"], p["date"]))
    state["new_laws"] = picked[:KEEP]
    msg = (f"새 법 감시: {FROM_DAYS // 7}~{TO_DAYS // 7}주 뒤 시행 {len(rows)}건 중 생활 분야 새로 다룰 것 {len(cands)}건 → 글감 {len(state['new_laws'])}건"
           + (f" — {', '.join(p['name'][:20] for p in state['new_laws'][:3])}" if state["new_laws"] else ""))
    add_log(state, True, msg)
    save_state(state)
    return msg
