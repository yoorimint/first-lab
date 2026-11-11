"""정부24 혜택 상세 원문 (공공서비스 API serviceDetail·supportConditions) — 혜택 글의 근거를 화면 대신 공식 자료 원본으로.

왜 (11/6 운영자 '가진 API 다 써서 더 정확하게'): 외부 검수가 고친 혜택 글 오류(#872 문의처 번호, 중복 지원 제외)가
정부24 상세 자료에 그대로 있었다. 지금까지는 목록(serviceList)만 써서 새 혜택·바뀐 혜택만 찾았다.

쓰는 곳:
  - 근거 대조(bot/source_check): 글이 정부24 혜택 주소(…/rcvfvrSvc/dtlEx/<서비스ID>)를 근거로 달면 화면 글자에 이 원문을 더해 숫자를 맞춰 봄
  - 외부 검수(automation/unreviewed.py): 검수 파일에 "benefit_detail" 로 넣어 검수자가 공식 원문을 바로 인용
  - 글쓰기 세션: python3 -m bot.benefit_detail <서비스ID> 로 원문을 읽고 씀 (daily-writer.md)
못 받으면 None — 예전처럼 화면만 본다.
"""
from __future__ import annotations

import re
import sys

from . import benefit_api

# 정부24 혜택 주소의 서비스ID (예: https://www.gov.kr/portal/rcvfvrSvc/dtlEx/WII000001600, …?svcId=627000000759)
_ID = re.compile(r"gov\.kr/portal/rcvfvrSvc/dtl(?:Ex)?/([A-Za-z0-9]{6,20})|[?&](?:svcId|serviceId|서비스ID)=([A-Za-z0-9]{6,20})")
FIELDS = ("서비스명", "소관기관명", "서비스목적", "지원대상", "선정기준", "지원내용", "신청기한", "신청방법", "구비서류",
          "접수기관명", "문의처", "법령", "자치법규", "행정규칙", "수정일시")
# supportConditions 는 대부분 Y/N 부호라 대조에 못 씀 — 나이 범위만 (JA0110 시작·JA0111 끝, 11/6 위기청소년 9·24 로 확인)
AGE_FROM, AGE_TO = "JA0110", "JA0111"


def service_ids(urls: list[str]) -> list[str]:
    out = []
    for url in urls:
        m = _ID.search(str(url))
        if m:
            out.append(m.group(1) or m.group(2))
    return list(dict.fromkeys(out))


def to_text(detail: dict, conditions: dict | None = None) -> str:
    """API 한 건 → '항목: 내용' 글자 (대조·인용용)."""
    lines = [f"{k}: {str(detail[k]).strip()}" for k in FIELDS if detail.get(k) not in (None, "", "해당없음")]
    if conditions and conditions.get(AGE_FROM) is not None and conditions.get(AGE_TO) is not None:
        lines.append(f"대상 나이: {conditions[AGE_FROM]}세~{conditions[AGE_TO]}세")
    return "\n".join(lines)


def _first(got: dict | None) -> dict | None:
    rows = (got or {}).get("data") or []
    return rows[0] if rows and isinstance(rows[0], dict) else None


PHONE = re.compile(r"(?<!\d)(0\d{1,2}-\d{3,4}-\d{4}|1\d{3}-\d{4})(?!\d)")
PAGE_URL = "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{sid}"


def _page(service_id: str) -> str:
    import urllib.request
    req = urllib.request.Request(PAGE_URL.format(sid=service_id), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", "replace")


def phone_note(api_text: str, page_html: str) -> str:
    """정부24 API 의 문의처 번호가 정부24 화면에 없으면 화면 번호를 덧붙임 (11/11 #3032: API 수정일 2025-11-27 '051-794-3755',
    화면(최종수정 2026.10.29)은 '051-794-3724' — API 가 늦게 바뀜). 화면을 못 읽으면 빈 글자."""
    api = set(PHONE.findall(next((l for l in api_text.splitlines() if l.startswith("문의처:")), "")))
    page = list(dict.fromkeys(PHONE.findall(page_html or "")))
    if not api or not page or api & set(page):
        return ""
    return f"※ 정부24 화면의 문의처 번호는 {', '.join(page[:3])} 예요 (API 번호 {', '.join(sorted(api))} 는 화면에 없음 — 화면 번호가 더 최신일 수 있어 화면 번호를 씀)"


def _fetch_text(service_id: str, fetch=None, page=None) -> str:
    fetch = fetch or benefit_api.fetch
    cond = {"cond[서비스ID::EQ]": service_id}
    detail = _first(fetch("serviceDetail", **cond))
    if not detail:
        return ""
    text = to_text(detail, _first(fetch("supportConditions", **cond)))
    try:
        note = phone_note(text, (page or _page)(service_id))
    except Exception:  # noqa: BLE001 - 화면을 못 열면 API 글자만
        note = ""
    return text + ("\n" + note if note else "")


def detail_text(service_id: str, fetch=None) -> str | None:
    """서비스ID 의 공식 원문 글자 (같은 날 다시 받지 않음). 없거나 못 받으면 None."""
    if fetch is not None:
        return _fetch_text(service_id, fetch) or None
    from .condition_gate import _cached
    return _cached("benefit", service_id, lambda: _fetch_text(service_id)) or None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("사용: python3 -m bot.benefit_detail <서비스ID 또는 정부24 혜택 주소> [...]")
        return 2
    code = 0
    for arg in argv[1:]:
        sid = (service_ids([arg]) or [arg])[0]
        text = detail_text(sid)
        print(f"== {sid} ==\n{text or '못 받음 (서비스ID 확인)'}\n")
        code = code or (0 if text else 1)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
