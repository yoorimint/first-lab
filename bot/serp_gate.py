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
COPY_MIN_CHARS = 18   # 띄어쓰기·문장부호 뺀 길이가 이만큼 넘는 문장이
COPY_MAX_SAME = 2     # 한 상위 글과 이만큼 넘게 똑같으면 베낀 것으로 봄 (법 조문 인용 한두 문장은 겹칠 수 있음)
COPY_CHECK_URLS = 8
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


# 같은 뜻의 다른 낱말 (11/12 #1082 '발급 비용' ↔ 본문 '수수료', #1197 '운영시간' ↔ '이용 시간', #1265 '월급' ↔ '임금'):
# 본문이 답하는데 낱말만 달라 '없음'으로 막히면 보강이 상위 글 질문을 기록에서 지워 버림 → 묶음 안 낱말은 서로 같은 것으로 봄
SYNONYMS = [
    {"운영시간", "영업시간", "이용시간", "시간"},
    {"비용", "수수료", "요금", "금액"},
    {"월급", "임금", "급여"},
    {"할인", "감면", "경감"},   # 11/12 #882 '3천원 할인' ↔ 본문 '3,000원 감면'
    {"변동", "변경"},
    {"기업", "회사", "사업장"},   # 11/12 #2151 '500인 기업' ↔ 본문 '500명 회사'
    {"동승", "탑승", "타고"},
    {"누락", "빠진", "빠졌", "빠지"},   # 11/12 #1750 '누락된 게 있다면' ↔ 본문 '공제가 빠졌다면'
    {"입금", "지급"},   # 11/12 #2078 '환급금은 언제 입금되나요?' ↔ 본문 '30일 안에 지급'
    {"소수점", "끝수"},   # 11/12 #214 '원 미만 소수점은?' ↔ 본문 '원 단위 끝수'   # 11/12 #861 '부모가 동승하지 않고' ↔ '부 또는 모가 반드시 타고'
    {"올리나", "올려", "올림", "올린"}, {"버리나", "버려", "버림", "버린"},   # 소수점 처리 질문 (#2151)
]


_COPULA = re.compile(r"(?:인가|일까|인지|이야|인데|이란|란|되나|됐나|되는지|될까|했다면|했나|할까|할|을까|나요|들|하나|거나|하고|해서|하려면|하는)$")  # '국가자격증인가요' → '국가자격증' (요는 _stems 가 뗌) · '신청하고' → '신청' ↔ 본문 '신청해요' (11/12 #1831)


def _splits(word: str, body: set[str]) -> bool:
    """붙여 쓴 합성어가 본문 낱말(2자 이상)로 나뉘는지 — 끝 한 글자(사·증 등)는 남아도 됨."""
    if len(word) < 4:
        return False
    ok = [True] + [False] * len(word)  # ok[i]: 앞 i 글자가 본문 낱말로 나뉨
    for i in range(len(word)):
        if ok[i]:
            for j in range(i + 2, len(word) + 1):
                if word[i:j] in body:
                    ok[j] = True
    return ok[-1] or (ok[-2] and len(word) >= 5)


def _split_covered(word: str, body: set[str]) -> bool:
    """붙여 쓴 합성어를 다룬 것으로 봄 (11/12 #477 '부동산경매사는 국가자격증인가요?' ↔ 본문 '부동산 경매 자격증').
    앞 한 글자만 붙은 것도 한 번 떼어 봄 (11/12 #1408 '앞브레이크' ↔ 본문 '브레이크')."""
    return _splits(word, body) or (len(word) >= 4 and (word[1:] in body or _splits(word[1:], body)))




def _prefix_covered(word: str, body: set[str]) -> bool:
    """본문 낱말이 이 말로 시작하면 다룬 것으로 봄 — 조사·말끝·뒤에 붙은 말 ('대상' ↔ '대상이었어요' #869, '신분' ↔ '신분증명서' #2159)."""
    return len(word) >= 2 and any(b.startswith(word) and len(b) > len(word) for b in body)


def _covered(word: str, body: set[str], body_joined: str = "") -> bool:
    """띄어쓰기만 다른 낱말도 같은 것으로 봄 (11/12 #1483 질문 '배시간' ↔ 본문 '배 시간')."""
    word = _COPULA.sub("", word) if len(word) >= 3 and len(_COPULA.sub("", word)) >= 2 else word
    return (word in body or any(any(word.startswith(g) for g in group) and any(b.startswith(g) for g in group for b in body) for group in SYNONYMS)
            or (len(word) >= 3 and word in body_joined) or _split_covered(word, body) or _prefix_covered(word, body))


_ASKING = {"얼마나", "어떤", "어떻게", "언제", "어디", "어디서", "무엇", "무슨", "누가", "누구", "왜", "몇",
           "될까", "되나", "될까요", "있으면", "없으면", "하면", "해야", "있을까", "있나", "찾을까", "가까운", "만약", "있다면"}  # 의문사·말끝 — 주제어가 아님 (11/12 #1408 '앞브레이크 하나만 있으면 될까?')


def uncovered(questions: list[str], body_html: str) -> list[str]:
    text = re.sub(r"<[^>]+>", " ", body_html or "")
    body, joined = _stems(text), re.sub(r"\s+", "", text)
    out = []
    for q in questions:
        norm = re.sub(r"(\d+)천\s*원", lambda m: f"{int(m.group(1)) * 1000:,}원", str(q))  # '3천원' ↔ 본문 '3,000원' (#882)
        words = _stems(norm) - _ASKING  # 의문사는 주제가 아니라 본문에 없어도 답일 수 있음 (11/12 #872·#823)
        if words and sum(_covered(w, body, joined) for w in words) < max(1, round(len(words) * COVER_RATIO)):
            out.append(str(q))
    return out


def written_plus(serp) -> bool:
    """쓸 때 상위 글 질문(+)과 더한 것(@)까지 넣은 글인지 (11/12 운영자 '새 초안은 보강해서 나오는 거 아냐?').
    그런 새 글은 +@ 보강 차례(automation/plus_queue.py)에서 빼고, 올릴 때 보강 끝 표시(refresh.PLUS_META)를 붙인다."""
    return isinstance(serp, dict) and not serp.get("unavailable") and bool(serp.get("questions"))


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


def _norm(text: str) -> str:
    return re.sub(r"[\s\u200b.,!?·'\"“”‘’()\[\]]+", "", text)


def copied(serp, body_html: str, get=None) -> list[str]:
    """상위 글 문장을 그대로 옮겼는지 (11/11 운영자 '상위 블로그 글하고 똑같이 적진 않겠지? 참고 조사만 하는 거지?').
    serp 의 상위 글을 다시 열어, 우리 본문의 긴 문장이 한 글과 COPY_MAX_SAME 개 넘게 똑같으면 막음. 못 열면 넘어감."""
    if not isinstance(serp, dict):
        return []
    if get is None:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
        import naver_top
        get = lambda u: naver_top.text_of(naver_top.fetch(naver_top.mobile(u)))[1]  # noqa: E731
    ours = [s for s in (_norm(x) for x in re.split(r"(?<=[.!?요다])\s+", re.sub(r"<[^>]+>", " ", body_html or ""))) if len(s) >= COPY_MIN_CHARS]
    out = []
    for url in [u for u in serp.get("urls") or [] if str(u).startswith("http")][:COPY_CHECK_URLS]:
        try:
            theirs = _norm(get(url))
        except Exception:  # noqa: BLE001 - 못 여는 글은 비교 못 함 (막지 않음)
            continue
        same = [s for s in ours if s in theirs]
        if len(same) > COPY_MAX_SAME:
            out.append(f"상위 글 문장을 그대로 옮김 {len(same)}곳 ({url[:60]}) — 참고만 하고 우리 말로 다시 쓰기")
    return out


def note(serp) -> str:
    """봇 기록용 한 줄 (검색 못 한 경우를 운영자·다음 실행이 볼 수 있게)."""
    if isinstance(serp, dict) and str(serp.get("unavailable") or "").strip():
        return f"상위 글 분석 못 함: {str(serp['unavailable'])[:60]}"
    return ""
