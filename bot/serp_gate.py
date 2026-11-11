"""상위 글 분석 관문 (11/11 운영자 '키워드 정해서 상위 글 확인하고 거기 있는 자료 + 없는 콘텐츠 넣고 있나').

지시서(daily-writer.md 3단계)에만 있던 '상위 글 5~10개의 질문·목차를 보고, 그 질문을 다 다루고 없는 것을 더하기'를
글쓰기 작업이 실제로 했는지 서버가 확인한다. 11/11 #2766 복비 계산기: 검색 1번뿐, 상위 글은 하나도 안 열고 씀.

새 글 JSON 의 "serp":
  {"urls": ["상위 글 주소", ...],            # 서로 다른 사이트 3곳 이상 (우리 사이트 빼고)
   "questions": ["상위 글이 다루는 질문", ...], # 3개 이상 — 우리 본문이 모두 다뤄야 함
   "added": "상위 글에 없는데 우리가 더한 것"}   # 계산 예시·계산기·상황별 판단·최신 개정 등
검색이 안 되면 {"unavailable": "이유"} — 막지 않고 봇 기록에만 남김 (평생 보류 막기).
막히면 초안 보류 → 다음 글쓰기 실행이 보류 글 고치기(5-1)로 빠진 질문을 채움.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .condition_gate import _stems

OWN_HOST = "first-lab.kr"
MIN_SITES = 3
MIN_QUESTIONS = 3
COVER_RATIO = 0.5   # 질문 낱말의 절반 이상이 본문에 있으면 다룬 것으로 봄


def _host(url: str) -> str:
    host = urlparse(str(url)).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _site(url: str) -> str:
    """같은 블로그 서비스의 다른 글쓴이는 다른 곳으로 (blog.naver.com/a ↔ blog.naver.com/b)."""
    p = urlparse(str(url))
    host = _host(url)
    first = p.path.strip("/").split("/")[0] if p.path.strip("/") else ""
    return f"{host}/{first}" if host in ("blog.naver.com", "m.blog.naver.com", "tistory.com", "brunch.co.kr") else host


def uncovered(questions: list[str], body_html: str) -> list[str]:
    body = _stems(re.sub(r"<[^>]+>", " ", body_html or ""))
    out = []
    for q in questions:
        words = _stems(str(q))
        if words and len(words & body) < max(1, round(len(words) * COVER_RATIO)):
            out.append(str(q))
    return out


def problems(serp, body_html: str) -> list[str]:
    if not isinstance(serp, dict):
        return ["상위 글 분석 기록(serp) 없음 — 상위 글 5~10개를 열어 질문·목차를 serp 에 적고, 그 질문을 모두 다룰 것 (daily-writer.md 3단계)"]
    if str(serp.get("unavailable") or "").strip():
        return []
    out = []
    urls = [u for u in serp.get("urls") or [] if str(u).startswith("http") and OWN_HOST not in _host(u)]
    if len({_site(u) for u in urls}) < MIN_SITES:
        out.append(f"상위 글 분석: 서로 다른 사이트 {MIN_SITES}곳 이상의 상위 글 주소가 필요 (지금 {len({_site(u) for u in urls})}곳)")
    questions = [q for q in serp.get("questions") or [] if str(q).strip()]
    if len(questions) < MIN_QUESTIONS:
        out.append(f"상위 글 분석: 상위 글이 다루는 질문 {MIN_QUESTIONS}개 이상 필요 (지금 {len(questions)}개)")
    missing = uncovered(questions, body_html)
    if missing:
        out.append(f"상위 글이 다루는데 본문에 없는 질문 {len(missing)}개: {' / '.join(m[:30] for m in missing[:3])} — 본문에 답을 넣기")
    if len(str(serp.get("added") or "").strip()) < 10:
        out.append("상위 글 분석: 상위 글에 없는데 우리가 더한 것(added)을 적기 — 계산 예시·계산기·상황별 판단·최신 개정 중 하나 이상")
    return out


def note(serp) -> str:
    """봇 기록용 한 줄 (검색 못 한 경우를 운영자·다음 실행이 볼 수 있게)."""
    if isinstance(serp, dict) and str(serp.get("unavailable") or "").strip():
        return f"상위 글 분석 못 함: {str(serp['unavailable'])[:60]}"
    return ""
