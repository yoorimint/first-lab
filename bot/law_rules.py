"""행정규칙(고시·훈령·예규)·자치법규(조례·규칙) 원문 — 법제처 API (admrul·ordin).

왜 (11/6 운영자 'API 다 써서 더 정확하게' 2번): 글 근거 중 행정규칙 주소 62곳·자치법규 14곳이 있는데
law.go.kr 의 '/행정규칙/이름'·'/자치법규/이름' 화면은 글자 없는 껍데기(스크립트로 채움)라 근거 대조가 '못 엶'으로 끝났고,
법령 감시도 법률·시행령만 봤다. 금액·요율은 고시(첨부 PDF)·조례 별표에 있는 경우가 많다.

쓰는 곳:
  - 근거 대조(bot/source_check): 그 주소면 화면 대신 이 원문 (고시는 첨부 PDF·hwpx 글자까지)
  - 조문 읽기(bot/lawtext): 법령에 없으면 행정규칙·자치법규에서 찾음 — `python3 -m bot.lawtext 대구광역시수도급수조례시행규칙 제18조의4`
  - 법령 감시(bot/law_watch): 글이 근거로 쓴 행정규칙·자치법규가 새로 개정되면 그 글에 재검증 표시, 시행 예정 판은 글감
못 받으면 None — 예전처럼.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET

from . import law_api

# 주소 속 종류 → (API target, 검색 결과 항목 태그, 이름 태그, 일련번호 태그, 본문 조회 키, 발령·공포일 태그)
KINDS = {
    "행정규칙": ("admrul", "admrul", "행정규칙명", "행정규칙일련번호", "ID", "발령일자"),
    "자치법규": ("ordin", "law", "자치법규명", "자치법규일련번호", "MST", "공포일자"),
}
TEXT_TAGS = ("조문내용", "조내용", "항내용", "호내용", "별표내용", "부칙내용", "부칙")
MAX_ATTACH = 3   # 고시 본문은 첨부(PDF)에만 있는 경우가 많음 (11/6 '2026년 적용 최저임금 고시' 조문내용 빈 칸)


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def rule_of(url: str) -> tuple[str, str] | None:
    """law.go.kr '/행정규칙/이름(/…)'·'/자치법규/이름(/…)' → (종류, 이름). 아니면 None."""
    plain = urllib.parse.unquote(str(url))
    m = re.search(r"law\.go\.kr/(행정규칙|자치법규)/([^/?#]+)", plain)
    return (m.group(1), m.group(2)) if m else None


def _date8(text: str) -> dt.date | None:
    return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8])) if re.fullmatch(r"\d{8}", text or "") else None


def versions(kind: str, name: str, fetch=None) -> list[dict]:
    """이름이 같은 판들 [{'seq', 'name', 'start'(시행일), 'issued'(발령·공포일), 'current'}] (시행일 순)."""
    fetch = fetch or law_api.fetch
    target, item, name_tag, seq_tag, _, issued_tag = KINDS[kind]
    xml = fetch("search", target, query=name, display="100")
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for r in root.iter(item):
        if _norm(r.findtext(name_tag) or "") != _norm(name):
            continue
        out.append({"seq": (r.findtext(seq_tag) or "").strip(), "name": (r.findtext(name_tag) or "").strip(),
                    "start": _date8((r.findtext("시행일자") or "").strip()), "issued": _date8((r.findtext(issued_tag) or "").strip()),
                    "current": (r.findtext("현행연혁구분") or "현행").strip() == "현행"})
    return sorted((v for v in out if v["seq"]), key=lambda v: (v["start"] or dt.date.min, v["seq"]))


def current(vers: list[dict], today: dt.date) -> dict | None:
    """오늘 시행 중인 판 (시행일이 오늘 이전인 것 중 가장 늦은 것)."""
    live = [v for v in vers if v["start"] and v["start"] <= today]
    return live[-1] if live else (vers[-1] if vers else None)


def _docs_text(xml: str, raw_of=None) -> str:
    from . import source_check
    raw_of = raw_of or source_check._raw
    texts = []
    # 자치법규 별표는 본문이 비고 첨부 hwp 로만 있음 — '<별표첨부파일명>' 칸 (11/7 #1288 고성군 당항포관광지 조례 요금표를 못 읽음)
    links = re.findall(r"<첨부파일링크>\s*([^<\s]+)", xml) + re.findall(r"<별표첨부파일명>\s*(?:<!\[CDATA\[)?\s*(https?://[^<\]\s]+)", xml)
    for link in links[:MAX_ATTACH]:
        got = raw_of(html.unescape(link).replace("http://", "https://"))
        texts.append(source_check.doc_text(got[0]) if got else "")
    return " ".join(t for t in texts if t)


def body_text(kind: str, seq: str, fetch=None, raw_of=None) -> str:
    """한 판의 본문 글자 (조문·별표·부칙 + 첨부 문서). 쉼표는 뺌 (숫자 대조용)."""
    fetch = fetch or law_api.fetch
    target, _, _, _, key, _ = KINDS[kind]
    xml = fetch("service", target, **{key: seq}) or ""
    if not xml:
        return ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    parts = [el.text or "" for tag in TEXT_TAGS for el in root.iter(tag)]
    text = html.unescape(re.sub(r"<[^>]+>", " ", " ".join(parts)))
    text += " " + _docs_text(xml, raw_of)
    return re.sub(r"\s+", " ", text).replace(",", "").strip()


def rule_text(kind: str, name: str, today: dt.date | None = None, fetch=None, raw_of=None) -> str | None:
    """이름으로 오늘 시행 중인 판의 본문. 못 찾으면 None. (서버·세션 모두 같은 날 다시 받지 않음)"""
    def make() -> str:
        now = current(versions(kind, name, fetch), today or dt.date.today())
        return body_text(kind, now["seq"], fetch, raw_of) if now else ""
    if fetch is not None:
        return make() or None
    from .condition_gate import _cached
    return _cached("rule", f"{kind}|{_norm(name)}", make) or None
