"""조건 누락 관문 (review_gate 가 부름) — 감시 세션의 외부 검수 없이도 '조건이 빠진 글'이 공개되지 않게.

11/5 운영자 '왜 못 막는데, 막을 대책 세워서 어떻게든 해야지, 평생 니가 고칠 거가'. 외부 검수에서 가장 자주 나온 두 가지를 코드로:
1. 제목 조건 (headline_scope): 제목·요약 숫자가 본문에서는 늘 같은 조건과 함께만 나오는데(P1·P2, 7월 1일 입사, 소형, 지역가입자 …)
   제목·요약에는 그 조건이 없으면 막는다 (#651 '7월 입사면 7.6일' — 본문은 7월 1일 입사, #655 '하루 최대 10,000원' — 본문은 P1·P2).
2. 다른 조문의 특례·예외 (exception_articles): 글이 근거로 단 조문(제N조)을 같은 법의 다른 조문이 '제N조에도 불구하고',
   '제N조에 따른 공제와 별개로'처럼 바꾸거나 더하면, 검수 기록이 그 조문도 확인했어야 통과 (#640 증여공제 제53조 ← 혼인·출산 제53조의2).
   글에 넣을 필요가 없으면 검수 기록에 그 조문을 '해당 없음 — 이유'로 적으면 된다. 법령을 못 열면 막지 않는다.
3. 첫 문단 단정 ↔ 본문 예외 (lead_vs_body, #748), 요약 '받아요' ↔ 본문 '못 받는 경우' (excerpt_vs_exclusions, #762),
   요약·첫 문단 기준 '하위 10%' ↔ 본문 '…이라면 하위 20%' (lead_thresholds, #771), 제목 '피해자' ↔ 본문은 늘 '불법사금융 피해자' (title_qualifier, #771),
   첫 문단 '3개월 이상 일해야' ↔ 본문 '일용근로자는 … 45일 이상이면' (lead_minimums, #775),
   갈래(5등급제·9등급제)를 나눠 설명하는 글에서 갈래 없이 '…만 적어요' (variant_only, #784),
   '법에 있는 상품 이름이 아니에요'처럼 법령 전체를 봐야 아는 부정 단정 (law_absence, #690).
4. 부칙 경과조치 (transition_rules, #762): 근거 조문이 최근 개정되고 부칙이 '시행 전에 이직한 사람은 종전 규정'이면,
   그 숫자를 쓴 문장에 적용 시점('2026년 1월 1일 이후 …', '종전')이 있어야 한다 (하루 상한 68,100원 ← 2025년 퇴사자는 66,000원).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.parse
from pathlib import Path

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


# 제목 길이 (11/6 운영자 '제목이 SEO에 맞나' → 1~5번): 공개 대기 104편 평균 37자·최대 55자에 사이트 이름까지 붙어 검색 결과에서
# 뒤가 잘림 (한글 25~30자 안팎 권장 — 구글 공식 기준은 없고 화면 폭으로 자름). 45자 넘으면 관문 보류, 35자 넘으면 글쓰기 사전 점검 경고.
TITLE_MAX, TITLE_WARN = 45, 35


def title_numbers(title: str) -> list[str]:
    """제목 속 숫자(연도 빼고) — 핵심 1개 권장 (11/6 #973 '3년마다 4시간, 교육비 32,000원' 세 개: 규칙은 바꿨는데 검사는 길이만 봄)."""
    return [n for n in re.findall(r"\d[\d,.]*", title or "") if not re.fullmatch(r"20\d\d", n)]


def title_length(title: str) -> list[str]:
    n = len((title or "").strip())
    return [f"제목 {n}자 — {TITLE_MAX}자 이하로 (검색 결과에서 뒤가 잘림). 키워드+찾는 말+핵심 숫자 1개, 덜 중요한 숫자는 요약문·본문으로"] if n > TITLE_MAX else []


# 첫 문단이 규칙을 그냥 말하는데(긍정 단정) 예외는 아래(FAQ 등)에만 — 11/5 #821·#828, 11/6 #928 '과태료 5회분' ↔ FAQ '전세사기피해주택은
# 과태료 부과 안 함'. 같은 꼴 3번 → 검사. 저장소 글 120편 실측 6편 걸림(맞는 것 1~2) → 공개를 막지 않고 글쓰기 사전 점검 경고로만.
EXCEPT_WORDS = re.compile(r"(제외|그러하지 아니|부과하지 않|적용하지 않|지지 않)")


def lead_vs_exceptions(body_html: str) -> list[str]:
    first = re.search(r"<p[^>]*>(.*?)</p>", body_html or "", re.S)
    if not first:
        return []
    rest = _sentences(body_html[first.end():])
    out = []
    for s in _sentences(first.group(0)):
        if EXCEPT_WORDS.search(s):
            continue
        stems = _stems(s)
        for e in rest:
            bare = re.sub(r"\([^)]*\)", "", e)  # '(보정 기간 등 제외)' 같은 괄호 속 제외는 뺌
            if EXCEPT_WORDS.search(bare) and len(stems & _stems(e)) >= 4:
                out.append(f"첫 문단 '{s[:50]}' 에 아래의 예외가 없음: '{e[:60]}' — 첫 문단에도 '(○○은 제외)'로")
                break
    return out[:2]


EXCEPTION_REF = r"(?:에도 불구하고|와 별개로|과 별개로)"


def related_exceptions(articles: dict[str, str], article: str) -> list[tuple[str, str]]:
    """같은 법의 본문 조문(부칙 제외) 중 article 을 '…에도 불구하고 / …와 별개로' 바꾸거나 더하는 조문 (번호, 제목)."""
    out = []
    for num, text in (articles or {}).items():
        if num == article:
            continue
        if (re.search(rf"{re.escape(article)}(?![의\d])[^.]{{0,40}}?{EXCEPTION_REF}", text or "")
                # '제N조에 따라 …하여야 하는 자는 … 대신 …할 수 있다' — 다른 방법을 허용하는 조문 (11/6 #960 부동산등기규칙 제60조의2:
                # 인감증명 대신 본인서명사실확인서, 글이 '인감증명이 필요해요'만 세 곳에 씀)
                or re.search(rf"{re.escape(article)}(?![의\d])에 따라[^.]{{0,40}}?대신", text or "")):
            head = re.match(rf"{re.escape(num)}\(([^)]{{1,40}})\)", text or "")
            out.append((num, head.group(1) if head else ""))
    return out[:6]


LAW_CACHE_DIR = Path(os.environ.get("FIRSTLAB_LAW_CACHE", "/tmp/firstlab-law-cache"))


def _cached(kind: str, key: str, make):
    """같은 날 같은 법령은 다시 받지 않음 (글쓰기 작업이 근거 대조를 여러 번 돌림 — 11/5 한 번에 9분)."""
    import datetime as _dt
    import hashlib
    path = LAW_CACHE_DIR / f"{kind}-{_dt.date.today():%Y%m%d}-{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    value = make()
    if value:  # 못 받은 것(빈 값)은 저장하지 않음 — 다음에 다시 시도
        try:
            LAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return value


def law_articles(base_url: str) -> dict[str, str]:
    return _cached("articles", base_url, lambda: _law_articles(base_url))


def _law_articles(base_url: str) -> dict[str, str]:
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
        if not _ok(item) or "해당 없음" in str(item.get("claim", "")):
            # '제N조 해당 없음 — 이유' 항목은 그 조문을 쓴 주장이 아니라 확인 기록 → 그 조문의 특례를 또 요구하지 않음
            # (11/5 일용직 4대보험 글: 해당 없음 항목마다 새 특례 조문이 요구돼 근거 대조를 9분씩 계속 다시 돌림)
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


# 갈래를 나눠 설명하는 글의 '…만' 단정 (11/5 #784: 5등급제·9등급제 글에서 '융합 선택 과목은 성취도만 적고',
# '과학탐구실험 … 3단계 성취도만' — 5등급제에만 맞는 말을 갈래 없이 씀, 같은 꼴 2번 → review-only-log 에서 검사로 옮김)
_VARIANT = re.compile(r"(\d+등급제|[가-힣]+ 융자|[가-힣]+가입자|[가-힣]+제도)")
_ONLY = re.compile(r"[가-힣A-Za-z)]만[ ,]+(?:적|받|돼|되|가능|해당|나와|내|써|인정|지원)")


def variant_only(body_html: str) -> list[str]:
    html = body_html or ""
    heads = [_plain(h) for h in re.findall(r"<h[23][^>]*>(.*?)</h[23]>", html, re.S)]
    variants = sorted(set(_VARIANT.findall(" ".join(heads))))
    if len(variants) < 2:
        return []
    out, head = [], ""
    for part in re.split(r"(<h[23][^>]*>.*?</h[23]>)", html, flags=re.S):
        if re.match(r"<h[23]", part):
            head = _plain(part)
            continue
        if any(v in head for v in variants):
            continue  # 갈래 이름이 붙은 부분 안의 문장은 그 갈래 이야기
        for sent in _sentences(part):
            if _ONLY.search(sent) and not any(v in sent for v in variants):
                out.append(f"'{sent[:50]}' 는 '…만' 단정인데 글이 {'·'.join(variants[:3])} 을 나눠 설명함 — 어느 갈래 이야기인지 적기")
    return out[:3]


# 법령 전체를 뒤져야 알 수 있는 '법에 … 없어요/아니에요' 단정 (11/5 #690 "'파킹통장'은 법에 있는 상품 이름이 아니에요" —
# 확인 못 한 것을 없다고 단정하는 유형 absence). 별칭이라고만 쓰면 뜻이 같다.
_LAW_ABSENCE = re.compile(r"(법|법령|법률|규정)(에|상|으로)[^.?!]{0,20}(이름|용어|상품|제도|규정|조항|근거)[^.?!]{0,10}(아니에요|아니고|없어요|없고|없습니다)")


def law_absence(body_html: str) -> list[str]:
    return [f"'{s[:50]}' 는 법령 전체를 봐야 아는 부정 단정 — '○○를 부르는 별칭이에요'처럼 확인한 사실로"
            for s in _sentences(body_html) if _LAW_ABSENCE.search(s)][:2]


# 요약 '○월 ○일부터 A에서 B로' ↔ 본문 '시행 이후 … 경우부터 적용' (11/5 #817 가산금 10%→40% — 그 전 신고분은 종전 10%.
# 같은 꼴: #762 상한액 적용 시점, #808 '11/27 이후 쓰는 휴가부터' → 3번째에 검사로)
_CHANGE = re.compile(r"(\d{1,2}월 \d{1,2}일|\d{4}년)(?: \d{1,2}월 \d{1,2}일)?부터[^.]*?\d[\d,.]*\s*(?:%|원|일|배)?\s*(?:에서|→)\s*\d")
_APPLY = re.compile(r"(이후|시행 후|시행 뒤)[^.]{0,60}?(경우|분|기간|것|휴가|신고)부터|종전")
_APPLY_OK = re.compile(r"이후|종전|분부터|경우부터|신고부터|전 [^.]{0,10}(은|는)")


# 요약 '○년 ○월 ○일부터 새 기준' ↔ 본문 부칙 경과조치('예전 기간 중 하나', '종전 규정') (11/5 #832 운전면허 갱신기간 —
# 2026년 전 발급자는 첫 갱신 때 예전 기간도 됨, #828 2027 고용보험 — 이미 가입한 사람은 이직 전까지 종전 규정)
_START = re.compile(r"\d{4}년 \d{1,2}월 \d{1,2}일부터")
_TRANSITION_BODY = re.compile(r"(종전|예전)의? (기간|규정|기준|방식|갱신기간)|중 하나(를|에) (따|골라|선택)")
_TRANSITION_OK = re.compile(r"예전|전에 [^.]{0,15}(받|가입|신청)")


def excerpt_apply_date(excerpt: str, body_html: str) -> list[str]:
    ex, body = _plain(excerpt), _plain(body_html)
    if _APPLY_OK.search(ex) or _TRANSITION_OK.search(ex):
        return []
    if _CHANGE.search(ex) and _APPLY.search(body):
        return [f"요약 '{ex[:50]}' 은 바뀐 값만 있고 본문의 적용 시점('이후 … 경우부터 적용', 그 전은 종전)이 없음 — 요약에도 넣기"]
    if _START.search(ex) and _TRANSITION_BODY.search(body):
        return [f"요약 '{ex[:50]}' 은 새 기준만 있고 본문의 경과조치('{_TRANSITION_BODY.search(body).group(0)}')가 없음 — 요약에도 넣기"]
    # '○일부터' 문장에 본문이 '지금도'라고 한 기존 값을 섞음 (11/5 #857: 범칙금 6만 원은 지금도 — 2/25부터 새로 생기는 건 벌점만)
    for sent in re.split(r"(?<=[.!?])\s+", ex):
        if not _START.search(sent) or _NOW.search(sent):
            continue  # 요약 문장이 스스로 '지금도'로 나눠 쓴 경우는 괜찮음
        for amount in re.findall(r"\d[\d,.]*\s*(?:만 원|원|%|점)", sent):
            if any(_NOW.search(b) and amount in b for b in _sentences(body_html)):
                return [f"요약 '{sent[:50]}' 은 '{amount}'까지 그날부터 생기는 것처럼 읽힘 — 본문은 '지금도'라고 함, 기존 값과 새 값을 나눠 쓰기"]
    return []


_NOW = re.compile(r"지금도|현재도|이미|현행")
