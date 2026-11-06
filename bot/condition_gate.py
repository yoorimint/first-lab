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


# 계산기 데이터가 다루는 기간 (11/6 #989 근무일수: 공휴일 목록이 2026~2027년뿐인데 글은 '대체공휴일까지 반영해 바로 세어 줘요' —
# 다른 해 날짜를 넣으면 공휴일이 빠진 값). data 에 holiday_from·holiday_to 가 있으면 본문에 그 기간('2026~2027년')이 있어야 함.
CALC_DATA_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "themes" / "first-lab" / "calculators" / "data"


def calc_coverage(body_html: str, data_dir: Path = CALC_DATA_DIR) -> list[str]:
    out = []
    for calc_id in dict.fromkeys(re.findall(r'\[firstlab_calc id="([a-z0-9-]+)"', body_html or "")):
        try:
            data = json.loads((data_dir / f"{calc_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        start, end = str(data.get("holiday_from", ""))[:4], str(data.get("holiday_to", ""))[:4]
        # '2026·2027년 월별 표' 같은 제목은 기간 안내가 아님 → '2026~2027년 … 공휴일' 처럼 범위와 공휴일을 같이
        if start and end and not re.search(rf"{start}\s*[~∼-]\s*{end}년[^.]{{0,25}}공휴일", _plain(body_html)):
            out.append(f"계산기 {calc_id} 의 공휴일 목록은 {start}~{end}년뿐 — 본문에 '{start}~{end}년 공휴일만 반영'처럼 기간을 밝히기")
    return out


# 요약 금액 앞의 조건이 본문보다 빠짐 (11/6 #1006: 본문 '금~일요일과 법정공휴일 15,000원' ↔ 요약 '금~일 15,000원' — 평일 공휴일도
# 13,000원으로 읽힘). 요약의 금액마다 본문 같은 금액 바로 앞(25자)의 날짜·시기 조건 낱말이 요약 쪽 앞(25자)에도 있어야 함.
WHEN_WORDS = ("공휴일", "성수기", "비수기", "주말", "평일", "명절", "연휴", "야간", "심야")


def excerpt_qualifiers(excerpt: str, body_html: str) -> list[str]:
    ex, body = _plain(excerpt or ""), _plain(body_html or "")
    out = []
    for m in re.finditer(r"\d[\d,]*\s?(?:만\s?)?원", ex):
        before = ex[max(0, m.start() - 25):m.start()]
        # 요약이 그 금액에 조건을 일부만 붙였을 때만 ('금~일 15,000원' — 본문은 '금~일요일과 법정공휴일'). 조건 없이 쓴 금액은
        # 다른 검사(lead_thresholds 등) 몫 — 저장소 127편 실측에서 조건 없는 금액까지 보면 4편 중 3편이 오탐
        if not (re.search(r"[월화수목금토일]\s?[~·-]\s?[월화수목금토일]", before[-12:]) or any(w in before[-12:] for w in WHEN_WORDS)):
            continue
        for b in re.finditer(re.escape(m.group(0)), body):
            if re.match(r"\s*[×x*=]", body[b.end():b.end() + 3]):
                continue  # 계산 예시 속 금액 ('비수기 일·월 2박 25,000원 × 2') — 요금 조건이 아님 (#998 오탐)
            missing = [w for w in WHEN_WORDS if w in body[max(0, b.start() - 25):b.start()] and w not in before]
            if missing:
                out.append(f"요약 '…{before[-12:]}{m.group(0)}' 에 본문의 조건 '{', '.join(missing)}' 이 빠짐 — 요약에도 넣기")
                break
    return out[:2]


# 요약이 본문의 규칙 문장만 옮기고 바로 뒤 '다만 …' 단서를 뺌 (11/6 #1026 해촉증명서: 요약 '간이지급명세서가 제출됐으면 증빙을 따로 안 붙여도 돼요'
# ↔ 본문 다음 문장 '다만 명세서에 누락·오류가 있어 공단이 확인하지 못하면 서류가 필요해요'). 같은 꼴(요약에 조건 없이 단정)이 자주 틀린 유형.
CAVEAT_START = re.compile(r"^(다만|단,|단 |하지만|그러나|예외로)")
CAVEAT_WORDS = re.compile(r"(다만|단,|제외|경우에만|만 해당|아니면|없으면|못하면)")


def excerpt_caveats(excerpt: str, body_html: str) -> list[str]:
    body = _sentences(body_html)
    out = []
    for s in _sentences(excerpt or ""):
        if CAVEAT_WORDS.search(s):
            continue
        stems = _stems(s)
        for i, b in enumerate(body[:-1]):
            if len(stems & _stems(b)) >= 4 and CAVEAT_START.search(body[i + 1]) and not CAVEAT_WORDS.search(" ".join(_sentences(excerpt))):
                out.append(f"요약 '{s[:40]}' 에 본문 바로 뒤 단서가 없음: '{body[i + 1][:50]}' — 요약에도 '다만 …'")
                break
    return out[:2]


# 올해만 하는 행사(무료·할인)를 연도 없이 '11월·12월 주말 무료'로 써서 해마다 그런 것처럼 읽힘 (11/6 #1028 태권도원 모노레일:
# 2026-11-06 보도자료 '연말까지 무료'). 공개가 몇 주~몇 달 뒤라 다음 해에도 남음 → 연도를 붙이게. 매년 같은 철 규칙(동절기 등)은 뺌.
LIMITED_EVENT = re.compile(r"(\d{1,2}월?\s*[·,~과와]\s*\d{1,2}월|연말까지|올해 말까지)[^.]{0,40}(무료|할인|이벤트|행사)"
                           r"|(무료|할인|이벤트|행사)[^.]{0,40}(\d{1,2}월?\s*[·,~과와]\s*\d{1,2}월|연말까지|올해 말까지)")
EVERY_YEAR = re.compile(r"(동절기|하절기|매년|해마다|성수기|비수기|20\d\d|올해)")


def limited_event_year(excerpt: str, body_html: str) -> list[str]:
    out = []
    for s in _sentences(excerpt or "") + _sentences(body_html or ""):
        if LIMITED_EVENT.search(s) and not EVERY_YEAR.search(s):
            out.append(f"'{s[:50]}' — 올해만 하는 행사면 '2026년 …'처럼 연도를 (해마다 같은 규칙이면 그대로)")
    return out[:3]


# 요약이 본문 괄호 속 제외 조건을 뺌 (11/6 #1134 국민연금 반환일시금: 본문 '사망했을 때(유족연금이 나오면 제외)' ↔ 요약 '사망').
PAREN_QUAL = re.compile(r"([가-힣]{2,8})(?:했을 때|할 때|일 때|인 때|된 때|한 때)?\(([^()]{2,40}?(?:제외|빼고|없을 때|아닐 때|만 해당|않을 때|안 나올 때|나오면))\)")


def excerpt_parentheticals(excerpt: str, body_html: str) -> list[str]:
    ex, body = _plain(excerpt or ""), _plain(body_html or "")
    out = []
    for m in PAREN_QUAL.finditer(body):
        word, qual = m.group(1), m.group(2)
        key = next((w for w in re.findall(r"[가-힣]{2,}", qual) if w not in ("제외", "경우", "해당")), "")
        if word in ex and key and key not in ex:
            out.append(f"요약의 '{word}' 에 본문 조건 '({qual})' 이 빠짐 — 요약에도 넣기")
    return list(dict.fromkeys(out))[:2]


# 다른 법률이 정한 예외 (같은 꼴 2번 → 검사, 11/6): #989 '4명 이하는 공휴일 유급휴일 아님' ↔ 노동절 제정법(5/1 은 규모 무관 유급휴일),
# #996 '가사 사용인은 근로기준법 미적용' ↔ 가사근로자법 제6조(인증기관 소속 가사근로자는 적용). 같은 법 안의 특례(exception_articles)로는 못 봄
# → 자주 쓰는 주제마다 알려진 다른 법 예외를 목록으로: 본문이 그 주제를 말하는데 예외 법의 말이 없으면 경고. 새로 찾으면 여기에 더한다.
CROSS_LAW = (
    (re.compile(r"(4명 이하|5명 미만|5인 미만)[^.]{0,60}(공휴일|유급휴일)|(공휴일|유급휴일)[^.]{0,60}(4명 이하|5명 미만|5인 미만)"),
     "노동절", "노동절(5월 1일)은 「노동절 제정에 관한 법률」로 사업장 규모와 상관없이 유급휴일"),
    (re.compile(r"가사\s?사용인"), "가사근로자",
     "인증 가사서비스 제공기관과 계약한 가사근로자는 가사 사용인으로 보지 않아 근로기준법 적용 (가사근로자법 제6조)"),
    # 11/6 #1036: '가사근로자는 근로기준법 제17조가 적용되지 않아요' — 대신 가사근로자법 제14조(서면 명시·교부)·제26조(벌금)가 있음
    (re.compile(r"가사근로자[^.]{0,80}(근로기준법|제17조)[^.]{0,30}(적용되지 않|적용 안|해당하지 않)"), "가사근로자법 제14조",
     "가사근로자는 근로기준법 대신 가사근로자법 제14조(근로조건 서면 명시·교부)·제26조(어기면 500만 원 이하 벌금)가 적용"),
)


def cross_law_exceptions(body_html: str) -> list[str]:
    text = _plain(body_html or "")
    return [f"본문이 '{m.group(0)[:30]}'를 말하는데 다른 법의 예외가 없음: {note}"
            for pattern, word, note in CROSS_LAW for m in [pattern.search(text)] if m and word not in text]


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


RULE_ARTICLE = re.compile(r"(?<![\w「])(제\d+조(?:의\d+)?)\s?\((?=[^)]{1,40}\))")
RULE_TAIL = re.compile(r"\s(?:부\s?칙|\[별표|■|\[별지)")


def rule_articles(text: str) -> dict[str, str]:
    """조례·고시 원문 글자 → {조문: 글자}. 조문 제목 '제7조(이용료의 반환)' 위치로 자르고, 마지막 조문은 부칙·별표 앞에서 끊음."""
    marks = list(RULE_ARTICLE.finditer(text or ""))
    out = {}
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        chunk = text[m.start():end]
        tail = RULE_TAIL.search(chunk)
        out.setdefault(m.group(1), (chunk[:tail.start()] if tail else chunk).strip())
    return out


def _law_articles(base_url: str) -> dict[str, str]:
    """'https://www.law.go.kr/법령/<이름>' → 현행 판 본문 조문 {번호: 글자} (법제처 API, 부칙 없음). 못 받으면 {}."""
    import datetime as dt
    from . import law_api
    from .law_rules import rule_of, rule_text
    rule = rule_of(base_url)
    if rule:  # 조례·고시도 조문 단서를 확인 (11/6 #1218 사천시 휴양림 조례 제7조② 단서 '긴급상황 시 다르게 적용' 누락)
        try:
            return rule_articles(rule_text(*rule) or "")
        except Exception:  # noqa: BLE001 - 못 받으면 막지 않음
            return {}
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


# 근거 조문 안의 단서·예외 문장 확인표 (11/6 운영자 '품질 1개 미만으로 — 1번 만들어라'): 외부 검수가 고친 곳의 74%가 '빠진 조건·예외'이고
# 대부분 글이 근거로 단 조문의 '다만 …'·'…은 제외한다'·'…에도 불구하고'였다 (#1026 시행령 제41조의2⑧ 단서, #1051 등).
# → 근거 조문마다 단서를 번호로 뽑아(bot.provisos 가 같은 번호로 보여 줌) 검수 기록에 '<조문> 단서N 반영/해당 없음 — 이유' 가 있어야 통과.
PROVISO_LIMIT = 6          # 조문 하나에서 확인할 단서 수 (앞에서부터)
PROVISO_MAX_MSGS = 8
PROVISO_DONE = re.compile(r"(반영|해당 ?없음|무관)")


def cited_articles(review: list[dict] | None) -> list[tuple[str, str]]:
    """검수 기록이 근거로 단 (법령 본문 주소, 조문) — 맞음·유지 주장만, '해당 없음' 확인 항목은 뺌."""
    from . import source_check
    from .review_gate import _ok
    out = []
    for item in review or []:
        if not _ok(item) or PROVISO_DONE.search(str(item.get("claim", ""))) and "단서" in str(item.get("claim", "")):
            continue
        if "해당 없음" in str(item.get("claim", "")):
            continue
        for url in item.get("urls", []):
            if "law.go.kr" in str(url):
                art = source_check.claim_article(item, str(url))
                key = (source_check.law_page(str(url)), art)
                if art and key not in out:
                    out.append(key)
    return out


def provisos_of(review: list[dict] | None, articles_of=None) -> list[tuple[str, str, int, str]]:
    """[(법령 이름, 조문, 번호, 단서 문장)] — 번호는 1부터 (글쓰기 세션 python3 -m bot.provisos 와 같은 번호)."""
    from .source_check import exceptions_in
    articles_of = articles_of or law_articles
    cache, out = {}, []
    for base, art in cited_articles(review):
        if base not in cache:
            cache[base] = articles_of(base) or {}
        name = urllib.parse.unquote(base).rsplit("/", 1)[-1]
        for k, x in enumerate(exceptions_in(cache[base].get(art, ""), limit=PROVISO_LIMIT), 1):
            out.append((name, art, k, x))
    return out


def article_provisos(review: list[dict] | None, articles_of=None) -> list[str]:
    """확인 안 한 단서 (검수 기록에 '<조문> 단서N' + 반영/해당 없음 이 없는 것). 조문을 못 읽으면 막지 않음."""
    claims = [str(i.get("claim", "")) for i in review or []]
    out = []
    for name, art, k, x in provisos_of(review, articles_of):
        key = re.compile(rf"{re.escape(art)}(?![의\d])\s*단서\s*{k}(?!\d)")
        if not any(key.search(c) and PROVISO_DONE.search(c) for c in claims):
            out.append(f"{name[:20]} {art} 단서{k} 확인 안 함: '{x[:60]}' — 글에 반영하고 검수 기록에 '{art} 단서{k} 반영 — 어디', "
                       f"관계없으면 '{art} 단서{k} 해당 없음 — 이유' (python3 -m bot.provisos 글.json)")
    return out[:PROVISO_MAX_MSGS]


# 근거 조문이 곧(UPCOMING_DAYS 안) 바뀌는데 글이 지금 규칙만 씀 (11/6 #1102 4대보험 EDI: 보험료징수법 제16조의10 단서가 2027-01-01 부터
# '월 60시간 미만' → '고용보험법상 소득기준 미만'으로 바뀜 — 공개가 몇 주 뒤라 공개 직후 틀린 글이 됨). 법제처 API 시행 예정 판으로 확인.
UPCOMING_DAYS = 180
UPCOMING_MAX_ARTICLES = 8


def _law_changes(name: str, art: str, today) -> list[dict]:
    """[{date, text}] — 같은 날 다시 받지 않음 (법제처 API 를 조문마다 여러 번 부름; '바뀌는 판 없음'도 저장)."""
    import datetime as dt
    from . import law_api
    if not law_api.available():
        return []

    def make() -> dict:
        try:
            got = law_api.upcoming_changes(name, art, today)
        except Exception:  # noqa: BLE001 - 못 읽으면 막지 않음 (저장도 안 함)
            return {}
        return {"changes": [{"date": f"{c['date']:%Y-%m-%d}", "text": str(c.get("text", ""))[:300]} for c in got]}
    data = _cached("upcoming", f"{name}|{art}", make) or {}
    return [{"date": dt.date.fromisoformat(c["date"]), "text": c["text"]} for c in data.get("changes", [])]


# 법제처 별표·서식 파일(flDownload)을 예전 판으로 근거 삼으면 개정 전 서식 내용을 그대로 옮긴다 (11/6 #1180 이직확인서:
# 소정근로시간 선택지·이직코드 22·23·연차수당 계산이 2020년 판). 파일 첫 줄 '<개정 날짜>'를 그 법령 현행 판의 같은 서식과 대조.
# (파일 번호 flSeq 는 내용이 같아도 판마다 달라 번호로는 대조 못 함)
FORM_FILE = re.compile(r"law\.go\.kr/LSW/flDownload\.do\?flSeq=\d+")


def _cited_form(url: str) -> dict:
    from . import law_api, source_check

    def make() -> dict:
        heads = law_api.form_heads(source_check._fetch(url) or "")
        return {k: f"{d:%Y-%m-%d}" for k, d in heads.items()}
    return _cached("formfile", url, make) or {}


def _current_form_dates(law: str) -> dict:
    import datetime as dt
    from . import law_api

    def make() -> dict:
        try:
            if not law_api.available():
                return {}
            version = law_api.current(law_api.versions(law), dt.date.today())
            return {k: f"{d:%Y-%m-%d}" for k, d in (law_api.form_dates(version) if version else {}).items()}
        except Exception:  # noqa: BLE001 - 못 읽으면 막지 않음 (저장도 안 함)
            return {}
    return _cached("formdates", law, make) or {}


def stale_forms(review: list[dict] | None, cited_of=None, current_of=None) -> list[str]:
    """근거 서식 파일의 개정일이 그 법령 현행 판의 같은 별표·서식보다 이르면 막음. 파일·현행 판을 못 읽으면 막지 않음."""
    cited_of, current_of = cited_of or _cited_form, current_of or _current_form_dates
    urls = sorted({u for i in review or [] for u in map(str, i.get("urls") or []) if FORM_FILE.search(u)})
    out = []
    for url in urls:
        for key, day in (cited_of(url) or {}).items():
            law, form = key.split("|")
            now = (current_of(law) or {}).get(key)
            if now and day < now:
                out.append(f"근거 서식 파일이 예전 판: {law} {form} — 파일은 {day} 개정판, 현행은 {now} 개정판 ({url}). "
                           f"법제처 그 법령의 '별표·서식' 현행 파일로 내용을 다시 대조하고 근거 주소도 바꾸기")
    return out


# 상시 4명 이하 사업장에 적용되는 근로기준법 조문 (시행령 별표 1). 항 단위로만 적용되는 조문(제19조①·제23조②·제55조①·제65조①③·
# 제70조②③)은 판단하지 않음. 가지 조문은 '부터 … 까지' 범위 안일 때만 (제74조의2·제76조의2 는 목록 밖).
_SMALL_RANGES = [(1, 13), (15, 15), (17, 18), (20, 22), (26, 26), (35, 42), (43, 45), (47, 49), (54, 54), (63, 64),
                 (66, 69), (71, 72), (74, 74), (76, 76), (78, 92), (101, 106), (107, 116)]
_SMALL_PARTIAL = {19, 23, 55, 65, 70}
_SMALL_TOPIC = re.compile(r"4\s*명\s*이하|5\s*[인명]\s*미만|규모가 작아도|작은 (?:회사|사업장)")
_SMALL_OK = re.compile(r"5\s*[인명]\s*이상|4\s*명\s*이하[^.]{0,20}(?:적용되지|적용 안|빠|제외)|별표\s*1")
_LABOR_REF = re.compile(r"제(\d+)조(?:의(\d+))?")
_OTHER_LAW = re.compile(r"(?<!근로기준)[가-힣]{2,}법(?:\s*시행령|\s*시행규칙)?\s*제\d+조")


def small_workplace_article(n: int, branch: int) -> bool | None:
    """근로기준법 제n조(의branch)가 상시 4명 이하에 적용되나 (모르면 None)."""
    if n in _SMALL_PARTIAL:
        return None
    for lo, hi in _SMALL_RANGES:
        if lo <= n <= hi and (not branch or n < hi):
            return True
    return False


def small_workplace_items(body_html: str) -> list[str]:
    """작은 사업장도 된다고 하는 근로기준법 글에서, 4명 이하에 적용되지 않는 조문을 조건 없이 안내한 문단
    (11/7 #1265 임산부 단축근무: '태아검진 시간 … (제74조의2)' — 별표 1 에 없는데 FAQ '회사 규모가 작아도 되나요? 네')."""
    html_ = body_html or ""
    if "근로기준법" not in html_ or not _SMALL_TOPIC.search(_plain(html_)):
        return []
    out = []
    for block in re.findall(r"<(?:p|li|td)\b[^>]*>(.*?)</(?:p|li|td)>", html_, re.S):
        text = _plain(block)
        if _SMALL_OK.search(text) or _OTHER_LAW.search(text):
            continue
        for n, b in _LABOR_REF.findall(text):
            if small_workplace_article(int(n), int(b or 0)) is False:
                art = f"제{n}조" + (f"의{b}" if b else "")
                out.append(f"근로기준법 {art} 는 상시 4명 이하 사업장에 적용되지 않음(시행령 별표 1) — 그 문단에 '상시 5명 이상 사업장' 조건을 적기: '{text.strip()[:50]}'")
                break
    return out[:3]


# 통상임금: 대법원 2024. 12. 19. 전원합의체(2020다247190)로 '고정성' 요건이 빠졌다 — 재직 조건이 붙거나 한 달을 넘는 주기로 나와도
# 정기적·일률적이면 통상임금 (11/7 #1232 해고예고수당 '기본급과 매달 정해진 금액으로 나오는 수당이 중심' → 정기상여금이 빠져 적게 계산).
OLD_WAGE_RULE = re.compile(r"고정성|고정적으로|고정적인|매달 정해진|매월 정해진|매달 나오는|매월 나오는|재직자에게만 주는")


def ordinary_wage_rule(body_html: str) -> list[str]:
    out = []
    for sent in re.findall(r"[^.?!。]+", _plain(body_html or "")):
        if "통상임금" in sent and OLD_WAGE_RULE.search(sent):
            out.append(f"통상임금을 옛 기준(고정성·매달 지급)으로 설명: '{sent.strip()[:60]}' — 2024.12.19 대법원 전원합의체 뒤에는 "
                       "재직 조건이 붙거나 한 달 넘는 주기라도 정기적·일률적이면 통상임금 (대법원 2018다296472 등)")
    return out[:2]


# '약 N만원'은 같은 글의 정확한 금액을 반올림한 값이어야 (11/6 #1212: 표 합계 34,428,900원을 '약 3,442만원'으로 버림 — 반올림 3,443만원).
APPROX_MAN = re.compile(r"약\s*([\d,]+)\s*만\s*원")
EXACT_WON = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3}){2,}|\d{7,})\s*원")


def approx_rounding(body_html: str) -> list[str]:
    text = re.sub(r"<[^>]+>", " ", body_html or "")  # 쉼표를 남겨야 금액을 읽음 (_plain 은 쉼표를 뺌)
    exact = {int(x.replace(",", "")) for x in EXACT_WON.findall(text)}
    out = []
    for raw in sorted(set(APPROX_MAN.findall(text))):
        n = int(raw.replace(",", ""))
        for x in sorted(exact):
            if x // 10000 == n and x % 10000 >= 5000:
                out.append(f"'약 {raw}만원' 은 본문 금액 {x:,}원을 버린 값 — 반올림하면 약 {n + 1:,}만원")
    return out[:3]


# 'N년간 보존'은 언제부터 세는지가 핵심 조건 (11/6 #1208 '계약서는 3년간 보존' — 근로기준법 시행령 제22조②: 근로관계가 끝난 날부터).
RETENTION = re.compile(r"[^.?!。]*\d+\s*년\s*(?:간|동안)?\s*(?:보존|보관)[^.?!。]*")
RETENTION_START = re.compile(r"부터|후|끝난|지난|기산|없")


def retention_start(body_html: str) -> list[str]:
    out = []
    blocks = re.sub(r"</(?:p|li|td|th|h\d|tr|caption)>", ". ", body_html or "")  # 표 칸·문단을 문장 경계로
    for m in RETENTION.finditer(_plain(blocks)):
        sent = m.group(0).strip()
        if not RETENTION_START.search(sent):
            out.append(f"보존 기간을 언제부터 세는지 없음: '{sent[-60:]}' — 근거 법령의 기산일(예: '근로관계가 끝난 날부터 3년')을 같이 쓰기")
    out += _retention_table(body_html)
    return out[:3]


def _retention_table(body_html: str) -> list[str]:
    """머리칸이 '보관·보존'인 표 열의 'N년' 칸 (11/7 #1428: '보관 기준' 열에 '임금채권 시효 3년' — 문장에 '보관' 말이 없어 위 검사가 못 봄)."""
    out = []
    for table in re.findall(r"<table\b.*?</table>", body_html or "", re.S | re.I):
        rows = [[_plain(c) for c in re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", r, re.S | re.I)]
                for r in re.findall(r"<tr\b.*?</tr>", table, re.S | re.I)]
        if not rows:
            continue
        cols = [i for i, head in enumerate(rows[0]) if re.search(r"보관|보존", head)]
        for row in rows[1:]:
            for i in cols:
                cell = row[i] if i < len(row) else ""
                if re.search(r"\d+\s*년", cell) and not RETENTION_START.search(cell):
                    out.append(f"보관 기간 칸을 언제부터 세는지 없음: '{row[0][:20]} — {cell[:40]}' — 기산일(예: '받을 수 있는 때부터 3년')과 법정 의무인지 권고인지 쓰기")
    return out


# 근거 조문이 '…범위에서 조례로 정하는 거리(기준) 이상'처럼 조례에 맡기는데 제목·요약·첫 문단이 법 하한을 그대로 단정
# (11/7 #1344 일조권: '10~17m 부분은 5m 이상만 띄우면 돼요' — 실제 거리는 시·군·구 조례, 서울 조례는 아직 높이의 1/2).
DELEGATED = re.compile(r"범위에서[^.]{0,40}조례로 정하는|조례로 정하는 (?:거리|기준|비율|면적|높이)")


def ordinance_delegation(title: str, excerpt: str, body_html: str, review: list[dict] | None,
                         articles_of=None, changes_of=None, today=None) -> list[str]:
    import datetime as dt
    first = re.search(r"<p[^>]*>.*?</p>", body_html or "", re.S)
    lead = f"{title} {excerpt} {_plain(first.group(0)) if first else ''}"
    if "조례" in lead:
        return []
    articles_of, changes_of = articles_of or law_articles, changes_of or _law_changes
    today = today or dt.date.today()
    for base, art in cited_articles(review)[:UPCOMING_MAX_ARTICLES]:
        texts = [(articles_of(base) or {}).get(art, "")]
        name = urllib.parse.unquote(base).rsplit("/", 1)[-1]
        texts += [str(c.get("text", "")) for c in (changes_of(name, art, today) or [])]
        windows = [t[max(0, m.start() - 300):m.end() + 300] for t in texts for m in DELEGATED.finditer(t)]
        # 첫 문단이 그 위임 범위의 숫자를 직접 말할 때만 (11/7 대기 글 검사: 양성화 글은 이행강제금 '5회분'만 말해 오탐)
        nums = {n for w in windows for n in re.findall(r"\d+(?:\.\d+)?", w) if len(n) >= 2 or "." in n}
        if nums & set(re.findall(r"\d+(?:\.\d+)?", lead)):
            return [f"{name} {art} 는 기준을 '조례로 정하는' 범위로 맡김 — 제목·요약·첫 문단에 '실제 기준은 시·군·구 조례로 정해요'를 적기 (법 기준은 최소·최대 범위)"]
    return []


def upcoming_amendments(review: list[dict] | None, body_html: str, changes_of=None, today=None) -> list[str]:
    import datetime as dt
    today = today or dt.date.today()
    changes_of = changes_of or _law_changes
    text = _plain(body_html or "")
    claims = [str(i.get("claim", "")) for i in review or []]
    out = []
    for base, art in cited_articles(review)[:UPCOMING_MAX_ARTICLES]:
        name = urllib.parse.unquote(base).rsplit("/", 1)[-1]
        for ch in changes_of(name, art, today) or []:
            day = ch.get("date")
            if not day or not (today < day <= today + dt.timedelta(days=UPCOMING_DAYS)):
                continue
            said = (f"{day.year}년 {day.month}월" in text or f"{day.year}.{day.month}.{day.day}" in text
                    or f"{day.year}. {day.month}. {day.day}" in text)
            noted = any(re.search(rf"{re.escape(art)}(?![의\d])[^.]{{0,20}}시행 ?예정", c) and PROVISO_DONE.search(c) for c in claims)
            if not (said or noted):
                out.append(f"{name[:20]} {art} 이 {day:%Y-%m-%d} 부터 바뀜 — 본문에 '{day.year}년 {day.month}월부터 …로 바뀌어요'를 쓰거나 "
                           f"검수 기록에 '{art} 시행 예정 해당 없음 — 이유' (python3 -m bot.lawtext {name} {art} 의 [시행 예정 개정])")
            break
    return out


# 시립·구립 등 공공시설 글은 그 시설의 '설치 및 운영 조례'가 입소·이용 대상과 요금을 정한다 (11/6 #1106 인천시립요양원: 보도자료만 보고
# 입소 대상을 '장기요양 수급자'로만 씀 — 조례 제5조①은 65세 이상 생계·의료급여 수급자, 시장 인정자도 대상). 근거에 그 조례가 있어야.
PUBLIC_FACILITY = re.compile(r"(시립|구립|군립|도립|공립)\s?[가-힣]{0,12}(요양원|요양시설|도서관|체육관|수영장|복지관|어린이집|박물관|미술관|캠핑장|휴양림|공원|병원|문화센터|청소년수련관)")


# 이름에 '군립·시립'이 없는 지자체 운영 시설 (11/7 #1288 당항포 오토캠핑장 '고성군', #1297 함허동천 야영장 '강화군시설관리공단':
# 군청 안내 페이지의 옛 요금을 그대로 옮김 — 조례 별표가 2026.1.1 개정) → 본문이 지자체·공단 운영이라고 하면 조례 확인
LOCAL_FACILITY = re.compile(r"(캠핑장|야영장|휴양림|수영장|체육관|도서관|관광지)")
LOCAL_RUNNER = re.compile(r"[가-힣]{1,6}(?:시|군|구)\s?(?:시설관리공단|시설공단|관광재단|문화재단)|[가-힣]{1,6}(?:시|군|구)(?:청|가|이|에서)\s?(?:직접\s?)?운영")


def facility_ordinance(title: str, review: list[dict] | None, excerpt: str = "", body_html: str = "") -> list[str]:
    # 제목에 '시립'이 없어도 요약에 있으면 (11/6 #1156 '남부대 수영장' — 요약 '남부대학교 시립국제수영장', 조례는 50% 감면인데 글은 30%)
    m = PUBLIC_FACILITY.search(title or "") or PUBLIC_FACILITY.search(excerpt or "")
    local = None if m else (LOCAL_FACILITY.search(title or "") and LOCAL_RUNNER.search(_plain(body_html or "")))
    if not m and not local:
        return []
    facility = re.sub(r"\s+", "", m.group(0)) if m else ""   # '시립요양원' — 다른 시설의 조례(계양구 치매전담형 …)로는 안 됨
    names = [re.sub(r"\s+", "", n) for i in review or [] for u in i.get("urls", [])
             for n in re.findall(r"/자치법규/([^/?#]+)", urllib.parse.unquote(str(u)))]
    claims = " ".join(str(i.get("claim", "")) for i in review or [])
    # 여러 시설을 함께 정하는 조례(광주광역시 체육시설 관리운영 조례 별표 1 '27. 남부대시립국제수영장')는 이름에 시설명이 없음
    # → 검수 기록에 '시설 조례: <조례 이름> …' 으로 그 조례가 이 시설을 정한다고 밝히면 통과
    if (any(facility in n for n in names) if facility else names) or "조례 해당 없음" in claims or (names and "시설 조례" in claims):
        return []
    return ["공공시설 글인데 그 시설의 설치·운영 조례를 근거로 안 씀 — 입소·이용 대상·요금은 조례가 정함 "
            "(python3 -m bot.lawtext <조례 이름> 제N조, 주소 https://www.law.go.kr/자치법규/<조례 이름>). 여러 시설 공통 조례면 검수 기록에 '시설 조례: <조례 이름> 별표 …', 조례가 없으면 '조례 해당 없음 — 이유'"]


# 예시 기한이 토·일·공휴일인데 '다음 날로 늦춰짐'을 안 씀 (11/6 #1126 증여세: '11월 6일에 받으면 2월 말일까지' — 2027-02-28 일요일,
# 3/1 삼일절 → 국세기본법 제5조로 3월 2일). 공휴일은 근무일 계산기 자료(calculators/data/work-days.json, 2026~2027).
EXAMPLE_MARK = re.compile(r"(예를 들어|예컨대|예시로|가령)")
POSTPONE = re.compile(r"(다음 ?날|그다음 ?날|늦춰|연장|미뤄|첫 평일|다음 평일)")


def _holidays() -> dict[str, str]:
    try:
        return json.loads((CALC_DATA_DIR / "work-days.json").read_text(encoding="utf-8")).get("holidays") or {}
    except (OSError, ValueError):
        return {}


def deadline_holiday(body_html: str, today=None) -> list[str]:
    import calendar
    import datetime as dt
    today = today or dt.date.today()
    holidays = _holidays()
    out = []
    for s in _sentences(body_html or ""):
        if not EXAMPLE_MARK.search(s) or POSTPONE.search(s):
            continue
        for m in re.finditer(r"(?:(20\d\d)년\s*)?(\d{1,2})월\s*(말일|\d{1,2}일)까지", s):
            month = int(m.group(2))
            if not 1 <= month <= 12:
                continue
            year = int(m.group(1)) if m.group(1) else (today.year if month >= today.month else today.year + 1)
            day = calendar.monthrange(year, month)[1] if m.group(3) == "말일" else int(m.group(3)[:-1])
            try:
                d = dt.date(year, month, day)
            except ValueError:
                continue
            if d.weekday() >= 5 or f"{d:%Y-%m-%d}" in holidays:
                why = "토요일" if d.weekday() == 5 else "일요일" if d.weekday() == 6 else holidays.get(f"{d:%Y-%m-%d}", "공휴일")
                out.append(f"예시 기한 '{m.group(0)}' 이 {d:%Y-%m-%d}({why}) — 기한이 토·일·공휴일이면 다음 날로 늦춰지는지(국세기본법 제5조 등) 쓰기")
    return out[:3]


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


# 제목·요약의 'N원부터'는 본문 금액 중 가장 낮은 값이어야 (11/7 #1272 BIM 자격증 '응시료 75,000원부터' — 본문 첫 문단에 필기 25,000원)
FROM_PRICE = re.compile(r"([\d,]{4,})\s*원\s*부터")
_FEE_WORD = re.compile(r"응시료|수수료|검정료|요금|이용료|입장료|가격|비용|사용료|수강료|필기|실기|1박|성인|어린이|1인")
_WON = re.compile(r"(?<![\d,])(\d{1,3}(?:,\d{3})+|\d{4,})\s*원")


def headline_from_price(title: str, excerpt: str, body_html: str) -> list[str]:
    out = []
    for where, text in (("제목", title or ""), ("요약", excerpt or "")):
        for m in FROM_PRICE.finditer(text):
            low = int(m.group(1).replace(",", ""))
            cheaper = sorted({int(w.replace(",", "")) for sent in re.findall(r"[^.?!]+", _plain_keep_commas(body_html))
                              if _FEE_WORD.search(sent) for w in _WON.findall(sent) if 0 < int(w.replace(",", "")) < low})
            if cheaper:
                out.append(f"{where} '{m.group(0)}' 인데 본문 {', '.join(f'{c:,}원' for c in cheaper[:3])} 이 더 낮음 — 가장 낮은 값으로 쓰거나 무엇의 금액인지(예: '필기+실기 합계') 밝히기")
    return out[:2]


def _plain_keep_commas(html_: str) -> str:
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", html_ or "")))


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
