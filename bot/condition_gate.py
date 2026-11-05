"""조건 누락 관문 (review_gate 가 부름) — 감시 세션의 외부 검수 없이도 '조건이 빠진 글'이 공개되지 않게.

11/5 운영자 '왜 못 막는데, 막을 대책 세워서 어떻게든 해야지, 평생 니가 고칠 거가'. 외부 검수에서 가장 자주 나온 두 가지를 코드로:
1. 제목 조건 (headline_scope): 제목·요약 숫자가 본문에서는 늘 같은 조건과 함께만 나오는데(P1·P2, 7월 1일 입사, 소형, 지역가입자 …)
   제목·요약에는 그 조건이 없으면 막는다 (#651 '7월 입사면 7.6일' — 본문은 7월 1일 입사, #655 '하루 최대 10,000원' — 본문은 P1·P2).
2. 다른 조문의 특례·예외 (exception_articles): 글이 근거로 단 조문(제N조)을 같은 법의 다른 조문이 '제N조에도 불구하고',
   '제N조에 따른 공제와 별개로'처럼 바꾸거나 더하면, 검수 기록이 그 조문도 확인했어야 통과 (#640 증여공제 제53조 ← 혼인·출산 제53조의2).
   글에 넣을 필요가 없으면 검수 기록에 그 조문을 '해당 없음 — 이유'로 적으면 된다. 법령을 못 열면 막지 않는다.
"""
from __future__ import annotations

import re
import urllib.parse

# 제목에 없으면 안 되는 '범위' 낱말: 대상·장소·기간·차종 등 (숫자가 이것과만 같이 나오면 조건)
SCOPE = re.compile(
    r"[A-Z]\d(?:·[A-Z]?\d)*"                  # P1·P2, P3
    r"|\d{1,2}월 \d{1,2}일"                    # 7월 1일 (입사일 등)
    r"|상시 \d+명 이상|만 \d+세 (?:이상|이하|미만)"
    r"|지역가입자|직장가입자|사업장가입자|임의계속가입자|소형|대형|경차|비사업용|사업용|중소기업|소상공인"
    r"|수도권|비수도권|무주택|1주택|다주택|미성년|기초생활수급자|차상위")
_SENT = re.compile(r"(?<=[.!?。요다])\s+|\n|</(?:p|li|tr|h\d)>")


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _sentences(html: str) -> list[str]:
    return [s for s in (_plain(x) for x in _SENT.split(re.sub(r"</td>", " ", html or ""))) if s]


def _num_pattern(n: str) -> re.Pattern:
    """'10000' → 10,000 / 10000 (앞뒤에 다른 숫자 없음)."""
    digits = n.replace(",", "")
    if "." in digits or len(digits) <= 3:
        body = re.escape(digits)
    else:
        head, rest = digits[: len(digits) % 3 or 3], digits[len(digits) % 3 or 3:]
        body = re.escape(head) + "".join(rf",?{rest[i:i + 3]}" for i in range(0, len(rest), 3))
    return re.compile(rf"(?<![\d.,]){body}(?![\d]|\.\d)")


def _is_date(token: str) -> bool:
    return bool(re.match(r"\d{1,2}월 \d{1,2}일$", token))


def _in_title(token: str, title: str) -> bool:
    """제목에 이미 있는 조건이거나, 제목과 상관없는 날짜면 True."""
    flat = title.replace("·", "").replace(" ", "")
    core = re.sub(r"\s*(?:이상|이하|미만)$", "", token)
    if core.replace("·", "").replace(" ", "") in flat:
        return True
    month = re.match(r"(\d{1,2})월 \d{1,2}일$", token)
    return bool(month) and f"{month.group(1)}월" not in title  # 날짜는 제목에 그 달이 있을 때만 조건 (#651 '7월 입사' ↔ 7월 1일)


def headline_scope(title: str, excerpt: str, body_html: str) -> list[str]:
    from .review_gate import _headline_numbers
    headline = title  # 요약에 조건이 있어도 제목만 보는 사람이 많음 (#655: 요약엔 P1·P2 가 있었지만 제목엔 없음)
    sentences = _sentences(body_html)
    out = []
    dateless = re.sub(r"\d{1,2}월(?:\s*\d{1,2}일)?", " ", title)  # '2월 17일'·'7월' 의 숫자는 날짜 자체라 비교 안 함
    dateless = SCOPE.sub(" ", dateless)  # 'P1·P2'·'만 19세 이상' 처럼 조건 자체에 든 숫자도
    for n in sorted(_headline_numbers(dateless)):
        pat = _num_pattern(n)
        hits = [s for s in sentences if pat.search(s)]
        if not hits:
            continue
        scoped = [{m.group(0) for m in SCOPE.finditer(s)} for s in hits]
        # 제목의 조건이 든 문장은 맞게 쓴 것 (P1·P2 와 P3 가 한 문장에 같이 나와도) → 제목에 없는 조건만 있는 문장만 셈
        scoped = [set() if any(_in_title(t, headline) and not _is_date(t) for t in toks) else {t for t in toks if not _in_title(t, headline)}
                  for toks in scoped]
        with_scope = [t for t in scoped if t]
        # 본문에 나오는 자리 절반 이상이 제목에 없는 범위 조건과 함께면 (#655: 10,000원은 P1·P2 월~목, P3 금~일 — 범위마다 다른 값)
        if len(with_scope) * 2 >= len(hits):
            tokens = sorted(set().union(*with_scope), key=lambda t: -sum(t in x for x in with_scope))[:4]
            out.append(f"제목 숫자 {n} 은 본문에서 '{', '.join(tokens)}' 같은 조건과 함께 나옴 — 제목에 그 조건을 넣거나, 조건 없이 맞는 값으로")
    return out


EXCEPTION_REF = r"(?:에도 불구하고|와 별개로|과 별개로)"


def related_exceptions(articles: dict[str, str], article: str) -> list[tuple[str, str]]:
    """같은 법의 본문 조문(부칙 제외) 중 article 을 '…에도 불구하고 / …와 별개로' 바꾸거나 더하는 조문 (번호, 제목)."""
    out = []
    for num, text in (articles or {}).items():
        if num == article:
            continue
        if re.search(rf"{re.escape(article)}(?![의\d])[^.]{{0,40}}?{EXCEPTION_REF}", text or ""):
            head = re.match(rf"{re.escape(num)}\(([^)]{{1,40}})\)", text or "")
            out.append((num, head.group(1) if head else ""))
    return out[:6]


def law_articles(base_url: str) -> dict[str, str]:
    """'https://www.law.go.kr/법령/<이름>' → 현행 판 본문 조문 {번호: 글자} (법제처 API, 부칙 없음). 못 받으면 {}."""
    import datetime as dt
    from . import law_api
    name = urllib.parse.unquote(base_url).split("/법령/")[-1].split("/")[0] if "/법령/" in urllib.parse.unquote(base_url) else ""
    if not name or not law_api.available():
        return {}
    try:
        version = law_api.current(law_api.versions(name), dt.date.today())
        return law_api.all_articles(version) if version else {}
    except Exception:  # noqa: BLE001 - 못 받으면 막지 않음
        return {}


def exception_articles(review: list[dict] | None, articles_of=None) -> list[str]:
    from . import source_check
    from .review_gate import _ok
    articles_of = articles_of or law_articles
    text_all = " ".join(str(i.get("claim", "")) + " " + " ".join(urllib.parse.unquote(str(u)) for u in i.get("urls", []))
                        for i in review or [])
    cache, done, out = {}, set(), []
    for item in review or []:
        if not _ok(item):
            continue
        for url in item.get("urls", []):
            if "law.go.kr" not in str(url):
                continue
            art = source_check.claim_article(item, str(url))
            base = source_check.law_page(str(url))
            if not art or (base, art) in done:
                continue
            done.add((base, art))
            if base not in cache:
                cache[base] = articles_of(base) or {}
            name = urllib.parse.unquote(base).rsplit("/", 1)[-1][:20]
            for num, head in related_exceptions(cache[base], art):
                if not re.search(rf"{re.escape(num)}(?![의\d])", text_all):
                    out.append(f"{name} {art} 의 특례·예외 조문 {num}({head}) 을 확인하지 않음 — 글에 반영하거나 검수 기록에 '{num} 해당 없음 — 이유' 항목으로")
    return out
