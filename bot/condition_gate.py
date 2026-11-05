"""조건 누락 관문 (review_gate 가 부름) — 감시 세션의 외부 검수 없이도 '조건이 빠진 글'이 공개되지 않게.

11/5 운영자 '왜 못 막는데, 막을 대책 세워서 어떻게든 해야지, 평생 니가 고칠 거가'. 외부 검수에서 가장 자주 나온 두 가지를 코드로:
1. 제목 조건 (headline_scope): 제목·요약 숫자가 본문에서는 늘 같은 조건과 함께만 나오는데(P1·P2, 7월 1일 입사, 소형, 지역가입자 …)
   제목·요약에는 그 조건이 없으면 막는다 (#651 '7월 입사면 7.6일' — 본문은 7월 1일 입사, #655 '하루 최대 10,000원' — 본문은 P1·P2).
2. 다른 조문의 특례·예외 (exception_articles): 글이 근거로 단 조문(제N조)을 같은 법의 다른 조문이 '제N조에도 불구하고',
   '제N조에 따른 공제와 별개로'처럼 바꾸거나 더하면, 검수 기록이 그 조문도 확인했어야 통과 (#640 증여공제 제53조 ← 혼인·출산 제53조의2).
   글에 넣을 필요가 없으면 검수 기록에 그 조문을 '해당 없음 — 이유'로 적으면 된다. 법령을 못 열면 막지 않는다.
3. 첫 문단 단정 ↔ 본문 예외 (lead_vs_body, #748), 요약 '받아요' ↔ 본문 '못 받는 경우' (excerpt_vs_exclusions, #762),
   요약·첫 문단 기준 '하위 10%' ↔ 본문 '…이라면 하위 20%' (lead_thresholds, #771), 제목 '피해자' ↔ 본문은 늘 '불법사금융 피해자' (title_qualifier, #771),
   첫 문단 '3개월 이상 일해야' ↔ 본문 '일용근로자는 … 45일 이상이면' (lead_minimums, #775).
4. 부칙 경과조치 (transition_rules, #762): 근거 조문이 최근 개정되고 부칙이 '시행 전에 이직한 사람은 종전 규정'이면,
   그 숫자를 쓴 문장에 적용 시점('2026년 1월 1일 이후 …', '종전')이 있어야 한다 (하루 상한 68,100원 ← 2025년 퇴사자는 66,000원).
"""
from __future__ import annotations

import datetime as dt
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


# 3. 첫 문단의 단정 ↔ 본문의 예외 (lead_vs_body): 첫 문단은 '…대상이 아니에요·없어요'로 단정하는데 본문에는 '…때·…이면 받을 수 있어요'가
#    있으면 막는다 — 첫 문단만 읽는 사람이 틀린 답을 가져감 (11/5 #748 '성인 Tdap 국가 지원 없음' ↔ 본문 '유행 때 임시예방접종 대상이면 지원').
ABSOLUTE_NEG = re.compile(r"(아니라|아니에요|아니고|없어요|없고|없어서|안 돼요|불가|못 받|받을 수 없|해당하지 않|지원하지 않)")
CONDITION = re.compile(r"(경우|때는|때만|때에|할 때|일 때|이면|라면|하면|다만|제외|빼고|아니면|한해|한정)")
PERMIT = re.compile(r"(받을 수 있|할 수 있|가능해|가능하|지원돼|지원해|지원을 받|지원받|돼요|대상이 돼|포함돼)")
JOSA = re.compile(r"(이라면|에서는|으로는|이면|라면|에는|으로|에서|에게|까지|부터|이라|이고|이나|은|는|이|가|을|를|의|에|도|로|과|와|만|요)$")
STOP = {"있어요", "있고", "해요", "돼요", "그래서", "하지만", "반대로", "이런", "같은", "따라", "때문", "정도", "모두", "아니라", "아니에요"}


def _stems(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[가-힣A-Za-z]{2,}", text):
        w = w.lower()
        stripped = JOSA.sub("", w)
        w = stripped if len(stripped) >= 2 else w  # '국가' 의 '가' 를 조사로 떼지 않게
        if len(w) >= 2 and w not in STOP:
            out.add(w)
    return out


def lead_vs_body(body_html: str) -> list[str]:
    first = re.search(r"<p[^>]*>(.*?)</p>", body_html or "", re.S)
    if not first:
        return []
    lead = _sentences(first.group(0))
    rest = _sentences(body_html[first.end():])
    out = []
    for s in lead:
        if not ABSOLUTE_NEG.search(s) or CONDITION.search(s):
            continue
        key = _stems(ABSOLUTE_NEG.split(s)[0][-40:])  # 단정 바로 앞말(무엇이 아닌지)
        for e in rest:
            if CONDITION.search(e) and PERMIT.search(e) and len(key & _stems(e)) >= 3:
                out.append(f"첫 문단 '{s[:50]}' 는 단정인데 본문에 예외가 있음: '{e[:60]}' — 첫 문단에도 그 조건을 넣기")
                break
    return out[:2]


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


# 요약 '받아요' ↔ 본문 '못 받는 경우' (11/5 #762 '…남기면, 12개월 근무 뒤 남은 구직급여의 절반을 받아요' ↔ 본문 '못 받는 경우' 목록)
EXCLUSION_HEAD = re.compile(r"<h[23][^>]*>([^<?]*(?:못 받|받을 수 없|제외|안 되는|대상이 아닌|지원 안|불가)[^<?]*)</h[23]>")  # 묻는 제목(…나요?) 제외
PROMISE = re.compile(r"(받아요|받을 수 있어요|지원돼요|돌려받아요|지원받아요)")
HEDGE = re.compile(r"(제외|빼고|못 받|않|단,|다만|경우|등은|확인|조건|대상)")


def excerpt_vs_exclusions(excerpt: str, body_html: str) -> list[str]:
    head = EXCLUSION_HEAD.search(body_html or "")
    if not head:
        return []
    for s in re.split(r"(?<=[.!?])\s+", _plain(excerpt)):
        if PROMISE.search(s) and not HEDGE.search(s):
            return [f"요약 '{s[:50]}' 는 조건 없이 받는다고 하는데 본문에 '{head.group(1).strip()}' 이 있음 — 요약에도 '(… 등은 제외)' 넣기"]
    return []


# 부칙 경과조치 (11/5 #762)
TRANSITION_DAYS = 730  # 개정 전 퇴사·신청자가 아직 받는 기간 (구직급여 최대 270일 + 조기재취업 청구 12개월 뒤)
_BUCHIK = re.compile(r"부\s*칙\s*<[^<>]{0,40}?(20\d\d)\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*>")
_TRANSITION = re.compile(r"개정\s*규정에도\s*불구하고[^.]{0,60}?종전")


def transitions(page_text: str, article: str, today: dt.date) -> list[tuple[dt.date, str]]:
    """법령 본문 글자(부칙 포함)에서 article 의 '시행 전 … 개정규정에도 불구하고 종전의 규정에 따른다' 중 최근·앞으로 시행되는 것."""
    text = page_text or ""
    heads = list(_BUCHIK.finditer(text))
    out = []
    for i, h in enumerate(heads):
        block = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        m = re.search(r"(20\d\d)년\s*(\d{1,2})월\s*(\d{1,2})일부터 시행", block)
        try:
            date = dt.date(*map(int, (m or h).groups()))
        except ValueError:
            continue
        if (today - date).days > TRANSITION_DAYS:
            continue
        for sent in re.split(r"(?<=다\.)\s+", block):
            ref = re.search(rf"{re.escape(article)}(?![의\d]|\s*\()(제\d+항)?(제\d+호)?", sent)  # '제14조(…경과조치)' 제목은 제외
            # 호(제N호)까지 짚은 경과조치는 그 호만 바뀐 것 — 조 단위로 단 주장과는 대개 무관 (오탐: 증여공제 제53조제4호·원천징수 제129조제1항제N호)
            if "시행 전" in sent and _TRANSITION.search(sent) and ref and not ref.group(2):
                out.append((date, re.sub(r"\s+", " ", sent).strip()[:160]))
    return out


def _nums(text: str) -> set[str]:
    """금액·비율 숫자 ('11만3500원' → '11만3500', '68,100' → '68100'), 조·항·호 번호와 날짜는 뺌."""
    text = re.sub(r"제\d+(?:조(?:의\d+)?|항|호)|\d{4}[.년]\s*\d{1,2}[.월]\s*\d{1,2}[.일]?", " ", text or "")
    return {n.replace(",", "") for n in re.findall(r"\d+(?:[.,]\d+)*(?:만\d*)?", text) if len(n.replace(",", "")) >= 2}


def changed_numbers(base_url: str, article: str, date: dt.date) -> set[str] | None:
    """그 개정(date 시행)으로 article 에 새로 생긴 숫자 (법제처 API 로 직전 판과 비교). 못 받으면 None."""
    from . import law_api
    name = urllib.parse.unquote(base_url).split("/법령/")[-1].split("/")[0]
    try:
        vers = law_api.versions(name)
        cur = next((v for v in vers if v["date"] == date), None) or law_api.current(vers, date)
        prev = [v for v in vers if cur and v["date"] < cur["date"]]
        if not cur or not prev:
            return None
        new, old = law_api.article(name, article, cur), law_api.article(name, article, prev[-1])
        return (_nums(new["text"]) - _nums(old["text"])) if new and old else None
    except Exception:  # noqa: BLE001 - 못 받으면 막지 않음
        return None


def _dated(sentence: str, date: dt.date) -> bool:
    y, m = date.year, date.month
    marks = ("종전", f"{y}년 {m}월", f"{y}.{m}.", f"{y}. {m}.", f"{y}년부터", f"{y}년 이후", f"{y - 1}년 이전", f"{y - 1}년까지")
    return any(k in sentence for k in marks)


def _page_text(base_url: str) -> str:
    from . import source_check
    return source_check.fetch(base_url) or ""


def transition_rules(review: list[dict] | None, body_html: str, page_of=None, changed_of=None,
                     today: dt.date | None = None) -> list[str]:
    """주장의 숫자가 최근 개정으로 바뀌었고 부칙이 '시행 전 …은 종전 규정'이면, 그 숫자를 쓴 본문 문장에 적용 시점이 있어야 함."""
    from . import law_api, source_check
    from .review_gate import _ok
    if page_of is None or changed_of is None:
        if not law_api.available():  # 법령을 받을 수 있는 곳(서버·글쓰기 작업)에서만 — 오프라인·테스트에서는 막지 않음
            return []
        page_of, changed_of = page_of or _page_text, changed_of or changed_numbers
    today = today or dt.date.today()
    sents = _sentences(body_html)
    cache, done, out = {}, set(), []
    for item in review or []:
        if not _ok(item):
            continue
        full = str(item.get("claim", ""))
        claim = full.split(" (")[0]
        nums = [n for n in re.findall(r"\d[\d,]*\d", claim) if len(n.replace(",", "")) >= 4]
        for url in item.get("urls", []):
            if "law.go.kr" not in str(url):
                continue
            art = source_check.claim_article(item, str(url))
            base = source_check.law_page(str(url))
            if not art or (base, art, claim) in done:
                continue
            done.add((base, art, claim))
            if base not in cache:
                try:
                    cache[base] = page_of(base) or ""
                except Exception:  # noqa: BLE001 - 못 받으면 막지 않음
                    cache[base] = ""
            for date, rule in transitions(cache[base], art, today):
                if not (_nums(full) & (changed_of(base, art, date) or set())):
                    continue  # 그 개정이 이 주장의 숫자를 바꾸지 않음 (오탐: 복권 원천징수 30%는 제129조 개정과 무관)
                hits = [s for s in sents if nums and any(_num_pattern(n).search(s) for n in nums)]
                if hits and not all(_dated(s, date) for s in hits):
                    name = urllib.parse.unquote(base).rsplit("/", 1)[-1][:20]
                    out.append(f"{name} {art} 는 {date:%Y.%m.%d} 개정, 부칙 '{rule[:60]}…' — '{hits[0][:40]}' 에 적용 시점"
                               f"('{date.year}년 {date.month}월 {date.day}일 이후 …, 그 전은 종전 기준')을 적기")
                    break
    return list(dict.fromkeys(out))[:3]


# 요약·첫 문단 기준값 ↔ 본문의 다른 기준값 (11/5 #771 '신용평점 하위 10%' ↔ '기초생활수급자·…이라면 하위 20%')
THRESHOLD = re.compile(r"(하위|상위|최대|최고|최소|최저|최장)\s*(\d[\d,.]*)\s*(%|점|만 원|원|년|개월|일|세)")


def lead_thresholds(excerpt: str, body_html: str) -> list[str]:
    first = re.search(r"<p[^>]*>.*?</p>", body_html or "", re.S)
    lead = _plain(excerpt) + " " + (_plain(first.group(0)) if first else "")
    rest = _sentences(body_html[first.end():] if first else body_html)
    out = []
    for m in THRESHOLD.finditer(lead):
        for sent in rest:
            if not CONDITION.search(sent):
                continue
            alt = next((a for a in THRESHOLD.finditer(sent) if a.group(1) == m.group(1) and a.group(3) == m.group(3)
                        and a.group(2) != m.group(2)
                        and not re.search(rf"{a.group(1)}\s*{re.escape(a.group(2))}\s*{re.escape(a.group(3))}", lead)), None)
            if alt:
                out.append(f"요약·첫 문단은 '{m.group(0)}' 만 적었는데 본문에 '{alt.group(0)}' 조건이 있음: '{sent[:60]}' — 요약·첫 문단에도 넣기")
                break
    return list(dict.fromkeys(out))[:2]


def title_qualifier(title: str, body_html: str) -> list[str]:
    """제목의 '…자'(피해자·대상자) 가 본문에서는 거의 늘 같은 꾸밈말과 나오는데 제목에는 없으면 (11/5 #771 '피해자' ↔ '불법사금융 피해자')."""
    body = _plain(body_html)
    out = []
    for word in sorted(set(re.findall(r"([가-힣]{2,}자)(?=[\s,·:]|$)", title or ""))):
        mods = re.findall(rf"([가-힣]{{2,}})\s+{word}", body)
        if len(mods) >= 3:
            top = max(sorted(set(mods)), key=mods.count)
            if mods.count(top) / len(mods) >= 0.6 and top not in title:
                out.append(f"제목 '{word}' 는 본문에서 늘 '{top} {word}' — 제목에도 '{top}' 를 넣기 (대상을 잘못 알 수 있음)")
    return out


# 요약·첫 문단 자격 기간 ↔ 본문의 대상별 다른 기간 (11/5 #775 '둘 다 3개월 이상 일하고 있어야' ↔ '일용근로자는 … 45일 이상이면')
MINIMUM = re.compile(r"(\d[\d,.]*)\s*(개월|년|일|시간)\s*(이상|이하|미만|초과)")
_WORK = re.compile(r"(일하|근무|근로|가입|거주|재직)")
_GROUP = re.compile(r"[가-힣]{2,}(?:근로자|자영업자|종사자|가입자|수급자|계층|가족|세대)(?:는|은|이라면|이면)")


def lead_minimums(excerpt: str, body_html: str) -> list[str]:
    first = re.search(r"<p[^>]*>.*?</p>", body_html or "", re.S)
    lead = _plain(excerpt) + " " + (_plain(first.group(0)) if first else "")
    rest = _sentences(body_html[first.end():] if first else body_html)
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", lead):
        if not _WORK.search(sent):
            continue
        for m in MINIMUM.finditer(sent):
            for other in rest:
                if not (_GROUP.search(other) and _WORK.search(other)):
                    continue
                alt = next((a for a in MINIMUM.finditer(other) if a.group(3) == m.group(3)
                            and (a.group(1), a.group(2)) != (m.group(1), m.group(2)) and a.group(0) not in lead), None)
                if alt:
                    out.append(f"요약·첫 문단은 '{m.group(0)}' 로 단정했는데 본문에 대상별로 '{alt.group(0)}' 이 있음: '{other[:60]}' — 첫 문단에도 그 예외를")
                    break
    return list(dict.fromkeys(out))[:2]
