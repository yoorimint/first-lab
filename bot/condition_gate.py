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


# 요약 숫자의 본문 한도 괄호 (11/9 #2362: 요약 '미신고 때 급여 50% 징수' ↔ 본문 '보험급여의 50%(… 산재보험료의 5배까지)').
# 본문에서 같은 숫자 바로 뒤 괄호가 한도(까지·한도·초과할 수 없)를 말하면, 요약에도 그 한도(괄호 안 숫자나 '한도')가 있어야 함.
_LIMIT_PAREN = r"\s*\(([^()]{2,50}?(?:까지|한도|초과할 수 없|넘을 수 없|넘지 않))\)"


def excerpt_number_limits(excerpt: str, body_html: str) -> list[str]:
    ex, body = _plain(excerpt or ""), _plain(body_html or "")
    out = []
    for num in dict.fromkeys(re.findall(r"\d[\d,.]*\s*(?:%|배|만\s?원|원)", ex)):
        for m in re.finditer(re.escape(num) + _LIMIT_PAREN, body):
            qual = m.group(1)
            keys = re.findall(r"\d[\d,.]*\s*(?:배|%|만\s?원|원|년|개월|시간|일)", qual)
            if keys and not any(k in ex for k in keys) and "한도" not in ex:  # 숫자 한도만 ('납부고지 전까지' 같은 시점은 뺌)
                out.append(f"요약의 '{num}' 에 본문 한도 '({qual})' 이 빠짐 — 요약에도 넣기")
                break
    return out[:2]


# 요약이 본문 첫머리의 '원칙적으로 N개월'에서 '원칙'을 뺌 (11/9 #2383: 요약 '신고기한부터 9개월 안에 세무서가 조사해 결정' ↔
# 본문 '이 결정은 원칙적으로 … 9개월 안에 해요. 부득이한 사유가 있으면 … 더 걸릴 수 있어요').
_PRINCIPLE = re.compile(r"(원칙적으로|원칙은|원칙상)")
_PERIOD = re.compile(r"\d[\d,.]*\s*(?:개월|일|년|주|%|만\s?원|원)")


_PROMISE = re.compile(r"(금액|요금|비용|보험료|수수료|가격|세금|과태료|벌금)")   # 돈 이야기 소제목 (+ '범위'·'얼마'가 같이 있을 때만)
_AMOUNT = re.compile(r"\d[\d,.]*\s*(?:만\s*)?(?:원|%|달러)")


def heading_without_amounts(body_html: str) -> list[str]:
    """금액·범위를 약속하는 소제목(h2) 아래에 금액이 하나도 없으면 (11/10 #34: 출처 없는 금액 표를 지운 뒤
    '차종별 연 보험료 대략 범위' 소제목만 남아 내용과 안 맞았음). 공식 안내·자주 묻는 질문 소제목은 뺌."""
    out = []
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", body_html or "", flags=re.S)
    for i in range(1, len(parts) - 1, 2):
        head = re.sub(r"<[^>]+>", "", parts[i]).strip()
        if "?" in head or "질문" in head or "바로가기" in head or not _PROMISE.search(head) or not re.search(r"범위|얼마", head):
            continue
        if not _AMOUNT.search(re.sub(r"<[^>]+>", " ", parts[i + 1])):
            out.append(f"소제목 '{head}' 은 금액·범위를 약속하는데 그 아래에 금액이 없음 — 소제목을 내용에 맞게 바꾸기")
    return out


def excerpt_principle(excerpt: str, body_html: str) -> list[str]:
    ex = _plain(excerpt or "")
    if _PRINCIPLE.search(ex):
        return []
    paras = re.findall(r"<p[^>]*>.*?</p>", body_html or "", re.S)[:2]
    out = []
    for sent in _sentences("".join(paras)):
        if not _PRINCIPLE.search(sent):
            continue
        for num in dict.fromkeys(_PERIOD.findall(sent[_PRINCIPLE.search(sent).end():])):
            listed = any(num in g and "·" in g for g in re.findall(r"\(([^()]*)\)", ex))  # '(5년·3개월)'처럼 예외 기간도 같이 적었으면 됨
            if num in ex and not listed:
                out.append(f"요약의 '{num}' 은 본문에서 '원칙적으로' (예외 있음) — 요약에도 '원칙적으로' 넣기")
    return out[:1]


# 법령 이름이 법제처에 있는 이름(정식 이름·공식 약칭)인지 (11/9 #2362 '어선원 재해보상보험법' — 정식은 '어선원 및 어선 재해보상보험법').
# 낱말 끝이 '법'인 것만 보고, 앞 낱말 1~3개를 붙여 본 이름 중 하나라도 법제처 정식 이름·약칭이면 맞음. 흔한 말(방법·개정법 등)은 뺌.
LAW_GENERIC = ("방법", "법률", "불법", "편법", "합법", "문법", "해법", "요법", "비법", "기법", "공법", "화법", "수법", "용법", "어법",
               "위법", "적법", "탈법", "입법", "사법", "무법", "개정법", "관련법", "한시법", "노동법", "행정법", "보호법", "특례법", "육성법",
               "본국법", "구별법", "특별법", "일반법", "현행법", "모법", "상위법", "하위법", "실정법", "형사법", "민사법", "세법", "작성법",
               "계산법", "사용법", "공부법", "보관법", "관리법", "신청법", "활용법", "정리법", "조리법", "세척법", "측정법", "표기법", "투자법",
               "절약법", "대처법", "해결법", "예방법", "운동법", "치료법", "복용법", "구분법", "확인법", "선택법", "고르는법", "읽는법", "쓰는법",
               "찾는법", "따는법", "보는법", "하는법", "내는법", "받는법", "구하는법", "대체역법", "특별조치법")
_LAW_SPLIT = re.compile(r"[\s·,/()「」『』\[\]:;]+")
_LAW_TOKEN = re.compile(r"^([가-힣]{2,}법)(?:에서|에는|에|은|는|이|가|을|를|의|으로|로|과|와|상|도|만|이나|에 따라)?$")


def _law_known(name: str) -> bool | None:
    """법제처에 정식 이름 또는 약칭으로 있는지 (띄어쓰기 무관). API 를 못 쓰면 None."""
    from . import law_api
    import xml.etree.ElementTree as ET
    key = re.sub(r"[\s·ㆍ]", "", name)

    def make():
        got = None
        for target in ("law", "eflaw"):  # eflaw: 아직 시행 전인 새 법도 (11/9 '2027년 5월부터 「장애인평생교육법」')
            xml = law_api.fetch("search", target, query=name.strip(), display="20")  # 띄어 쓴 이름 그대로 (붙이면 못 찾는 판이 있음)
            if not xml:
                continue
            got = False
            for law in ET.fromstring(xml).iter("law"):
                names = (law.findtext("법령명한글") or "", law.findtext("법령약칭명") or "")
                if key in (re.sub(r"[\s·ㆍ]", "", n) for n in names):
                    return True
        return got
    return _cached("law_known", key, make)


def unknown_law_names(body_html: str, known=_law_known) -> list[str]:
    toks = [t for t in _LAW_SPLIT.split(_plain(body_html or "")) if t]
    out = []
    for i, tok in enumerate(toks):
        m = _LAW_TOKEN.match(tok)
        if not m:
            continue
        name = m.group(1)
        if name.endswith(LAW_GENERIC) or name in out:
            continue
        prev = [re.sub(r"[^가-힣]", "", t) for t in toks[max(0, i - 3):i]]
        cands = [name] + [" ".join(prev[j:] + [name]) for j in range(len(prev) - 1, -1, -1) if prev[j]]
        results = [known(c) for c in cands]
        if None in results and not any(results):
            continue  # 법제처를 못 열면 막지 않음
        if not any(results):
            out.append(name)
    return [f"법령 이름 '{n}' 이 법제처 정식 이름·약칭에 없음 — 정식 이름으로 (예: 「어선원 및 어선 재해보상보험법」)" for n in out[:3]]


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


def _no_surrogates(value):
    """짝 잃은 서로게이트 문자 제거 — 법제처 행정규칙 본문에 섞여 와서 캐시 저장(utf-8)이 실패하고 근거 대조 전체가 멈춤 (11/9 #2161)."""
    if isinstance(value, str):
        return re.sub(r"[\ud800-\udfff]", "", value)
    if isinstance(value, dict):
        return {k: _no_surrogates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_no_surrogates(v) for v in value]
    return value


def _cached(kind: str, key: str, make):
    """같은 날 같은 법령은 다시 받지 않음 (글쓰기 작업이 근거 대조를 여러 번 돌림 — 11/5 한 번에 9분)."""
    import datetime as _dt
    import hashlib
    path = LAW_CACHE_DIR / f"{kind}-{_dt.date.today():%Y%m%d}-{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    value = _no_surrogates(make())
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
PROMISE = re.compile(r"(받아요|받을 수 있어요|지원돼요|돌려받아요|지원받아요|줄어요|감면돼요|면제돼요)")  # 11/7 #1766 '가산세 50%가 줄어요'
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
        # 본문은 같은 기준값 바로 뒤 괄호에 다른 값을 둠: '최대 5년(특례 15년)' ↔ 요약 '최대 5년' (11/8 #2066 — 보관 글 279편 중 이 글만 걸림)
        unit = re.escape(m.group(3))
        pat = re.escape(m.group(0)).replace(r"\ ", r"\s*") + rf"\s*\(([^()]{{0,30}}?)(\d[\d,.]*)\s*{unit}\)"
        for a in re.finditer(pat, _plain(body_html[first.end():] if first else body_html)):
            if a.group(2) != m.group(2) and not re.search(rf"{re.escape(a.group(2))}\s*{unit}", lead):
                out.append(f"요약·첫 문단은 '{m.group(0)}' 만 적었는데 본문은 '{a.group(0)}' — 괄호 속 예외도 요약·첫 문단에 넣기")
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


# 제목 '넣은 돈 → 받는 돈' 이 여러 공제율 중 한 구간 값인데 '최대' 같은 말이 없으면 (11/9 #2295 'IRP·DC 900만 원 채우면 148만 5,000원':
# 16.5% 구간 값, 본문에 같은 900만 원이 13.2% 구간이면 118만 8,000원). 제목 두 금액 A·M 이 M = A×p (p 는 본문 비율)이고,
# 본문에 다른 비율 q 로 계산한 A×q 금액도 나오면 걸림.
_KO_WON = re.compile(r"(?:(\d[\d,]*)\s*억\s*)?(?:(\d[\d,]*)\s*만\s*)?(\d[\d,]*)?\s*원")
_RATE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%")
TIER_HEDGE = re.compile(r"(최대|최고|까지|이하|구간|라면|이면|기준)")


def _won_amounts(text: str) -> set[int]:
    out = set()
    for m in _KO_WON.finditer(text or ""):
        eok, man, rest = (int(g.replace(",", "")) if g else 0 for g in m.groups())
        if eok or man or rest:
            out.add(eok * 100_000_000 + man * 10_000 + rest)
    return out


def title_tier_amount(title: str, excerpt: str, body_html: str) -> list[str]:
    if TIER_HEDGE.search(title or ""):
        return []
    text = _plain(excerpt) + " " + _plain(body_html)
    rates = {float(r) for r in _RATE.findall(text) if 0 < float(r) <= 50}
    amounts = _won_amounts(title)
    body_amounts = _won_amounts(text)
    out = []
    for a in amounts:
        for m in amounts:
            hit = next((p for p in rates if m != a and round(a * p / 100) == m), None)
            others = [q for q in rates if q != hit and round(a * q / 100) in body_amounts and round(a * q / 100) != m] if hit else []
            if others:
                out.append(f"제목 '{m:,}원' 은 {hit:g}% 구간 값 — 본문에 같은 {a:,}원이 {others[0]:g}% 면 {round(a * others[0] / 100):,}원. 제목에 '최대' 또는 구간 조건을 넣기")
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
_LAW_ABSENCE = re.compile(r"(법|법령|법률|규정)(에|상|으로)[^.?!]{0,20}(이름|용어|상품|제도|규정|조항|근거)[^.?!]{0,10}(아니에요|아니고|없어요|없고|없습니다)"
                          r"|(?<![가-힣])(법률|법령|법적) ?(근거|규정|조항) ?없음")  # 표 칸 '법률 근거 없음' (11/7 #1901)


# 원문 전체를 다 봐야 알 수 있는 '자료·안내·규칙에 … 없어요/정해져 있지 않아요' 단정 (11/11 +@ 보강에서 5번: #1752 '발표 자료에는
# 70% 숫자가 없어요'가 실제로는 붙임 PDF에 있었음, #1481·#1718·#1403·#3213). '…에 따르면 없어요'처럼 출처를 밝힌 말은 뺌.
_SOURCE_ABSENCE = re.compile(
    r"(?<![가-힣])(법|법령|법률|규정|규칙|고시|시행령|자료|안내|공고|발표 자료)(?:에는|에선|에서는|에|엔|상|은|는)\s(?!따르|따라)"
    r"[^.?!]{0,28}(정해져 있지 않|나와 있지 않|적혀 있지 않|없어요|없습니다|없고)"
    r"|(?:규정|조항|근거)(?:은|이) 없어요")


def source_absence(sentences: list[str]) -> list[str]:
    return [f"'{s[:50]}' 는 원문 전체를 봐야 아는 '없다' 단정 — 원문이 실제로 정한 내용으로 바꿔 쓰기 (11/11 #1752)"
            for s in sentences if _SOURCE_ABSENCE.search(s)][:3]


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


# 주휴수당 요건 '개근' (근로기준법 시행령 제30조①) — 본문엔 있는데 요약·첫 문단이 '주 15시간 이상이면 주휴'로만 씀
# (11/7 #1601 알바 급여, #1419 주휴수당 뜻 등 같은 꼴 반복 → 검사로)
_ATTEND = re.compile(r"개근|다 나오|모두 나오|빠짐없이 나오|결근 없이|다 나온|모두 나온")


def holiday_attendance(excerpt: str, body_html: str) -> list[str]:
    first = re.search(r"<p[^>]*>.*?</p>", body_html or "", re.S)
    lead = _plain(excerpt or "") + " " + (_plain(first.group(0)) if first else "")
    body = _plain(body_html or "")
    if "주휴" in lead and re.search(r"15\s*시간", lead) and _ATTEND.search(body) and not _ATTEND.search(lead):
        return ["요약·첫 문단이 주휴수당 요건을 '주 15시간 이상'만 적음 — 본문처럼 '그 주 소정근로일 개근'도 넣기 (시행령 제30조①)"]
    return []


# 무급휴무일과 겹친 공휴일엔 유급휴일수당 없음 — 노동절(5월 1일)은 법률로 날짜를 정한 유급휴일이라 예외 (11/7 #1607,
# 고용노동부 상담 '휴무일과 근로자의 날이 중복되는 경우라면 … 유급휴일수당 및 휴일근로가산임금을 지급'; 2027.5.1 토요일)
_NO_HOLIDAY_PAY = re.compile(r"무급\s*휴무일[^.]{0,40}공휴일|공휴일[^.]{0,40}무급\s*휴무일")


def labor_day_exception(body_html: str) -> list[str]:
    out = []
    for block in re.findall(r"<(p|li)[^>]*>(.*?)</\1>", body_html or "", re.S):
        text = _plain(block[1])
        if _NO_HOLIDAY_PAY.search(text) and re.search(r"없|더하지 않|안 받|받지 않", text) and not re.search(r"노동절|근로자의 ?날", text):
            out.append(f"무급휴무일과 겹친 공휴일 규칙에 노동절 예외가 없음: '{text[:50]}' — 노동절(5월 1일)은 무급휴무일과 겹쳐도 시급제·일급제 유급휴일수당")
    return out[:2]


# 해마다 새 공고로 바뀌는 사업(정책자금·채용·시험)의 금액을 제목·요약이 연도 없이 씀 — 한 회차 규칙을 일반 규칙처럼
# (11/7 #1423 소방안전관리자·#1504 장애인고용공단 채용·#1678 소상공인 정책자금 — 하루 세 번 → 검사로)
_NOTICE = re.compile(r"(20\d\d)년[^.。<]{0,40}(공고|융자계획|사업계획|시행계획)")
_HEAD_AMOUNT = re.compile(r"\d[\d,.]*\s*(만\s*원|억|천만\s*원|원|%)")


def notice_year_label(title: str, excerpt: str, body_html: str) -> list[str]:
    body = _plain(body_html or "")
    if len(_NOTICE.findall(body)) >= 2 and _HEAD_AMOUNT.search(title or "") and not re.search(r"20\d\d", f"{title} {excerpt}"):
        return ["본문은 'O년 공고' 기준인데 제목·요약의 금액에 연도가 없음 — 해마다 공고로 바뀌는 값이면 제목이나 요약에 'O년 공고 기준'을 넣기"]
    return []


# 법이 하위 법령에 맡긴 값을 글이 그대로 옮김 — 시행령까지 읽으면 숫자가 있음 (11/7 #1705 '신고일부터 4주 범위에서
# 대통령령으로 정한 기간' → 시행령 제71조의2 '2주'). 아직 하위 법령이 없는 개정 예정 내용은 그렇다고 쓰면 통과.
_DELEGATED = re.compile(r"(?:대통령령|시행령|시행규칙|총리령|[가-힣]+부령)(?:으로|에서) 정(?:한|하는|해진)\s*(?:기간|비율|일수|기한|한도)")
_DELEGATED_OK = re.compile(r"아직|정해지지|나오지 않|미정|입법예고|\d+월 \d+일부터")


def unresolved_delegation(body_html: str) -> list[str]:
    out = []
    for s in re.split(r"(?<=[.?!])\s+|\n+", _plain(body_html or "")):
        if _DELEGATED.search(s) and not _DELEGATED_OK.search(s):
            out.append(f"'{_DELEGATED.search(s).group(0)}' 를 그대로 옮김 — 시행령·시행규칙 조문을 열어 실제 값(예: 2주)으로 쓰기"
                       f" (아직 정해지지 않았으면 그렇다고 쓰기): {s.strip()[:60]}")
    return out


# 중과 제외 '수도권·광역시·세종 밖' 3억 원 이하 주택: 밖으로 보는 읍·면은 광역시만이 아니라 지방자치법 제3조③④의
# 읍·면 전체(수도권·세종 포함) — 글이 '광역시 군·읍·면'으로 좁혀 씀 (11/7 #1734·#1759, 소득세법 시행령 제167조의3①1호)
_COUNTY_NARROW = re.compile(r"광역시(?:에 속한|에 소속된|의)?\s*군(?:과|·|,)\s*읍·?면")


def county_scope(body_html: str) -> list[str]:
    for s in re.split(r"(?<=[.?!])\s+|\n+", _plain(body_html or "")):
        if "3억" in s and _COUNTY_NARROW.search(s):
            return ["'광역시 군·읍·면'으로 좁혀 씀 — 수도권·광역시·세종 밖으로 보는 곳은 광역시의 군과 지방자치법 제3조③④의"
                    f" 읍·면 전체(수도권·세종의 읍·면 포함) (소득세법 시행령 제167조의3①1호): {s.strip()[:60]}"]
    return []


# 읽기 쉽게 (11/7 운영자 '글 가독성은 체크 안 하나' → 기준 승인): 목록 항목 200자·문단 300자·문장 100자 이하,
# 법 조문 괄호 인용은 요소 하나에 1개까지. 화면 나누기(mu-plugins/first-lab-readability.php)와 같은 기준.
READ_LI_MAX, READ_P_MAX, READ_SENT_MAX, READ_REFS_MAX = 200, 300, 100, 1
LAW_REF = re.compile(r"\([^()<>]{0,60}(?:법|령|규칙|규정|고시)[^()<>]{0,30}제\d+조[^()<>]{0,60}\)")


def split_sentences(text: str) -> list[str]:
    """'.?!' 뒤 공백에서 자름 — 괄호 안은 자르지 않음 ('(…제1항. 다만…)')."""
    out, start, depth = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch in ".?!" and depth == 0 and i + 1 < len(text) and text[i + 1].isspace():
            out.append(text[start:i + 1].strip())
            start = i + 1
    if text[start:].strip():
        out.append(text[start:].strip())
    return out


def readability_element(tag: str, inner_html: str) -> list[str]:
    """요소 하나(문단·목록 항목 안쪽 HTML)의 읽기 문제."""
    text = _plain(inner_html)
    limit = READ_LI_MAX if tag == "li" else READ_P_MAX
    out = []
    if len(text) > limit:
        out.append(f"{'목록 항목' if tag == 'li' else '문단'}이 {len(text)}자 (기준 {limit}자) — 나누거나 줄이기: {text[:30]}")
    lines = re.split(r"<br\s*/?>", inner_html or "")  # 한눈에 보기 상자 ✔ 줄은 마침표 없이 <br> 로 나뉨 — 줄마다 따로 (11/10 #1734 상자가 한 문장 106자로 잡힘)
    for s in [x for line in lines for x in split_sentences(_plain(line))]:
        if len(s) > READ_SENT_MAX:
            out.append(f"문장이 {len(s)}자 (기준 {READ_SENT_MAX}자) — 두 문장으로: {s[:30]}")
    refs = LAW_REF.findall(text)
    if len(refs) > READ_REFS_MAX:
        out.append(f"법 조문 괄호가 {len(refs)}개 — 본문에는 1개까지, 나머지 근거는 글 끝 근거 칸으로: {refs[1][:30]}")
    return out


def readability(body_html: str, limit: int = 5) -> list[str]:
    out = []
    for tag, inner in re.findall(r"<(li|p)(?:\s[^>]*)?>((?:(?!</?(?:li|p|ul|ol)\b).)*)</\1>", body_html or "", flags=re.S):
        out += readability_element(tag, inner)
    return [f"읽기: {x}" for x in out[:limit]]


# 평균임금 = 3개월 임금 ÷ 총일수만 쓰고 '통상임금보다 적으면 통상임금' (근로기준법 제2조②)을 빠뜨림 (11/7 #1858 프리랜서 퇴직금)
AVG_WAGE_DEF = re.compile(r"평균임금[^.?!]{0,40}(?:3개월|석 달)[^.?!]{0,80}(?:나눈|나눠|나누)")


def average_wage_floor(body_html: str) -> list[str]:
    text = _plain(body_html or "")
    m = AVG_WAGE_DEF.search(text)
    if m and "통상임금" not in text:
        return [f"평균임금 계산을 설명했는데 '통상임금보다 적으면 통상임금으로'(근로기준법 제2조②)가 없음: {m.group(0)[:50]}"]
    return []


# 제목 금액이 그해에만 맞는 특례·한시 값인데 제목에 연도가 없음 (11/7 #1929 재산세 '3억이면 299,400원' —
# 1주택 공정시장가액비율 43~45%·특례세율이 2026년분까지라 2027년에는 틀린 제목)
_ONE_YEAR = re.compile(r"(20\d\d)년분[^.?!]{0,40}(?:에 한해|에만|까지 적용|까지만)")


def year_only_headline(title: str, excerpt: str, body_html: str) -> list[str]:
    m = _ONE_YEAR.search(_plain(body_html or ""))
    if m and _HEAD_AMOUNT.search(title or "") and not re.search(r"20\d\d", title or ""):
        return [f"본문은 '{m.group(0)[:30]}' (그해에만 맞는 값)인데 제목 금액에 연도가 없음 — 제목에 '{m.group(1)}년' 넣기"]
    return []


# 연차 사용 촉진 → 보상 의무 면제는 '사용 기간이 끝나 소멸한' 연차만 (근로기준법 제61조①: '제60조제7항 본문에 따라 소멸된 경우').
# 퇴사로 남은 연차까지 못 받는 것처럼 읽힘 (11/9 #2179, 같은 꼴 #1471 — 보관 글 279편 중 이 두 글만 걸림)
_LEAVE_NO_PAY = re.compile(r"보상\s*(?:의무가|할 의무가|하지 않아도|안 해도)")
_LEAVE_GONE = re.compile(r"(소멸|없어진|사라진|사용 기간이 끝나|기간이 지나)")


def leave_promotion_extinction(body_html: str) -> list[str]:
    for sent in _sentences(body_html or ""):
        if re.search(r"(연차|휴가)", sent) and _LEAVE_NO_PAY.search(sent) and not _LEAVE_GONE.search(sent):
            return [f"연차 사용 촉진의 보상 면제는 사용 기간이 끝나 소멸한 연차만(근로기준법 제61조①) — 그 조건이 없음: '{sent[:60]}'"]
    return []



# 법 원문으로 틀렸다고 확인한 문구 (같은 오류가 여러 글에 되풀이됨 — 11/10 쉬운 말 다듬기 뜻 검수에서 발견). 문장 단위: (그 문장에 있어야 할 말, 틀린 문구, 고칠 말)
KNOWN_WRONG = [
    (r"중도상환", r"3년 이내에? 갚을 때만", "중도상환수수료 예외는 3년 이내 상환만이 아님 — '3년 이내에 갚을 때나 다른 법령이 허용할 때 등, 법이 정한 경우에만' (금융소비자보호법 제20조①4호나목, #303·#219)"),
    (r"바우처|이용권", r"분만예정일(?:로)?부터 2년", "임신 바우처 사용 기간은 신청 시기와 관계없이 출산일(유산·사산일)부터 2년 (국민건강보험법 시행령 제23조⑥1호, #1917)"),
    (r"해외주식|국외주식|해외 주식", r"5년 이상[^.]{0,20}(?:주소|거소|거주)", "국외주식 양도세는 거주자면 대상 — '5년 이상 국내 거주' 조건은 국외 부동산 등 국외자산만 (소득세법 제94조①3호다목, 제118조의2 3호 삭제 2019.12.31, #1718)"),
    (r"금융소비자보호법", r"(?<!지역 )농협[·, ]*수협 같은 상호금융", "농협은행·수협은행·신협은 금융소비자보호법 대상 — 빠지는 건 새마을금고·지역 농협·수협 같은 조합 (금소법 제2조6호가목, 시행령 제2조①2호, #1841)"),
    (r"촉구|촉진|지정 통보|서면 통보", r"(?:하나라도|한 단계라도|중 하나라도) 빠(?:지|졌)", "2차 지정 통보는 근로자가 촉구 뒤 10일 안에 사용 시기를 안 알렸을 때만 필요 — '1차 촉구가 없거나, 필요한 2차 통보가 빠지면' (근로기준법 제61조①2호, #1798·#2179)"),
    (r"실업급여", r"실업급여의? 정식 (?:이름|명칭)은 구직급여", "실업급여는 구직급여와 취업촉진 수당을 함께 이르는 말 — '흔히 실업급여라고 부르는 돈은 정확히는 구직급여' (고용보험법 제37조, #1800)"),
    (r"유증", r"(?<!선순위인 )(?<!선순위 )상속인이 아닌 (?:사람|자)에게", "배우자공제를 줄이는 유증은 '선순위인 상속인이 아닌 자에게' 한 것 — 후순위 상속인 포함 (상속세 및 증여세법 제24조1호, #2062·#1754)"),
    # 비율 '반올림'은 2026-11-27 시행 개정(제9조①, 부칙 제3조)대로 11월분부터라 맞는 말 — 막지 않음 (#254)
    (r"^(?!.*반올림).*장기요양", r"(?<!\d)11월분부터",
     "장기요양보험료 13.14%(소득 대비 0.9448%)는 2026년 1월분부터 — 11월분부터 바뀌는 건 비율 반올림뿐 (노인장기요양보험법 시행령 제4조·법 제9조①, #1455·#254)"),
    (r"^(?=.*체납)(?=.*환급금).*", r"충당될 수 있", "체납 국세가 있으면 국세환급금은 반드시 먼저 충당 — '충당될 수 있어요' 아님 (국세기본법 제51조②2호 '충당하여야 한다', #2078)"),
    (r"^(?!.*간편장부)(?=.*연말정산).*(보험 ?설계사|보험 ?모집인|방문 ?판매원|음료품 ?배달원|연말정산(하는|되는|된|까지 해 주는|해 주는|을 받은|받은) 사업소득)",
     r"만 있는 (사람|경우)|신고하지 않아도|신고 안 해도|신고를 안 해도|신고할 필요(가|는)? 없|신고 예외|예외예요",
     "연말정산된 사업소득만 있어 종합소득세 신고를 안 해도 되는 건 '간편장부대상자'만 (소득세법 제73조①4호·시행령 제137조①, #469)"),
    (r"연세액|연납", r"10만\s*원\s*미만", "연세액 10만 원 '이하' 자동차세는 1기분에 전액 부과할 수 있음 — '미만' 아님 (지방세법 제128조④, #1093)"),
    ("본문:" + r"양도소득세|취득세|종합부동산세|중과", r"조정대상지역은[^.]{0,40}위축",
     "세금 중과의 조정대상지역은 '주택 분양 등이 과열됐거나 과열될 우려가 있는' 지역(주택법 제63조의2①1호)만 — 위축 지역(2호)은 아님 "
     "(소득세법 제104조⑦·지방세법 제13조의2, #1759·#1734)"),
    (r"경정청구", r"종합소득세를? 신고한 (?:사람|거주자)[^.]{0,40}지방소득세[^.]{0,20}경정청구",
     "개인지방소득세 경정청구 의무는 '개인지방소득세 확정신고를 한 거주자' — 2020년부터 종합소득세와 따로 신고 (지방세법 제96조①·제95조①, #1686)"),
    (r"본문:(?=.*경정청구)(?=.*중소기업 (?:취업자 )?소득세 감면)", r"^(?!.*(?:150만|2023)).*200만 원(?:이 한도|까지| 한도)", "중소기업 취업자 감면 한도는 2022년 귀속분까지 150만 원, 2023년분부터 200만 원 — 지난 5년을 돌려받는 경정청구 글은 둘 다 (조세특례제한법 제30조①, 부칙 제19199호 2022.12.31, #3118)"),
    (r"본문:기초연금", r"^(?!.*연계).*별정우체국[^.]*(?:수급권자|받는)[^.]*(?:빠져|제외|못 받)", "직역연금 수급권자 제외에 '직역재직기간 10년 이상 연계퇴직연금·연계퇴직유족연금' 수급권자도 들어감 (기초연금법 제3조③4호, #3080)"),
    (r"귀속", r"귀속(?:연도|년도)는 (?:돈을 |급여를 )?받은 해", "근로소득 귀속연도는 일한 해(근로를 제공한 날) — 12월분을 다음 해 1월에 받아도 그 전해 귀속 (소득세법 시행령 제49조①1호, 제135조②, #3097)"),
    (r"본문:^(?=[\s\S]*(?:임금|급여)명세서)(?=[\s\S]*과태료)", r"(?:근로자|직원) ?1명(?:마다|당)(?![^.]*(?:줘야|교부|작성))", "임금명세서 과태료를 근로자 수대로 매긴다는 규정은 없음 (근로기준법 시행령 별표 7 일반기준·개별기준 사목, 11/11 #1455)"),
    (r"본문:두루누리", r"고용보험[과·]\s?국민연금[^.]*(?:자격|가입 ?이력)[^.]*(?:없|얻은 적)", "두루누리 신규가입자는 보험마다 따로 — 고용보험료는 직전 1년 고용보험 취득이력, 국민연금은 최초가입이거나 직전 1년 국민연금 사업장 가입 이력 (고용노동부고시 고용보험료 지원 제6조①, 보건복지부고시 연금보험료 지원 제5조①, #3205)"),
    (r"8세 이상 20세 이하|자녀", r"^(?!.*(?:예전|옛|바뀌기 전|개정 전|이전 기준)).*(?:12,500원|29,160원|1명당 25,000원)",
     "8~20세 자녀 간이세액 차감액은 2026.3.1 원천징수분부터 1명 20,830원·2명 45,830원·3명 이상 1명당 33,330원 더 (소득세법 시행령 별표 2 제3호, 대통령령 제36129호 부칙 제15조, 11/11 #553)"),
    ("본문:" + r"추납|추후납부", r"^(?!.*(?:9\.5%|같은 달|12월 안에|그 달 안에)).*12월에 신청하면[^.]*10%",
     "12월에 추납을 신청하고 같은 12월 안에 내면 그해 보험료율(2026년 9.5%)로 계산 — 다음 달 기한에 내면 10% (법률 제21146호 부칙 제3조, 11/11 #3228·#3134)"),
    # 8/3 세제개편안은 9/1 국무회의 정부안 확정 때 일부 철회됨 (재정경제부 2026.9.1 보도자료 첨부 p.4·5, 11/11 #451·#2110)
    ("본문:" + r"종합부동산세|종부세", r"(?:살지 않는|비거주)\s?1주택[^.]{0,40}9억",
     "9/1 정부 수정안: 비거주 1주택 기본공제는 12억 원 그대로 (8/3안 9억 원 철회, 재정경제부 2026.9.1 보도자료, #451)"),
    ("본문:" + r"종합부동산세|종부세", r"세부담 상한(?:을|도)? ?(?:200%로 올리|바꾸)(?![^.]*(?:빠|철회|유지))",
     "9/1 정부 수정안: 세부담 상한 150% 그대로 (8/3안 200% 철회, 재정경제부 2026.9.1 보도자료, #451)"),
    (r"해고예고수당", r"^(?!.*(동시|해고 때|해고하는 그때|다른 금품|월급|퇴직금)).*14일 ?(이내|안)", "해고예고수당은 퇴직 뒤 14일 이내가 아니라 해고와 동시에 지급 (고용노동부 빠른인터넷상담 202408290318189250579; 14일은 제36조 금품 청산 기한, #1232)"),
]


# 정해진 날부터 틀리게 되는 문구 (시행 예정 개정, 법 원문 확인): (그날, 틀리게 되는 말, 같은 문장에 있으면 괜찮은 말, 고칠 말)
DATED_WRONG = [
    (dt.date(2026, 12, 8), r"근로감독관", r"노동감독관",
     "2026년 12월 8일부터 '노동감독관' (근로기준법 제101조 개정, 노동감독관 직무집행법 — 11/10 #2161 다듬기에서 확인)"),
]


def known_wrong(body_html: str, today: dt.date | None = None) -> list[str]:
    """법 원문으로 확인한, 여러 글에 되풀이된 틀린 문구 (+ 시행일이 지나 틀리게 된 말)."""
    today = today or dt.date.today()
    out = []
    whole = _plain(body_html or "")
    for sent in _sentences(body_html):
        for topic, wrong, fix in KNOWN_WRONG:
            # 주제 앞에 '본문:' 이 붙으면 글 전체에서 주제를 봄 (뜻풀이 문장엔 주제어가 없을 때 — 조정대상지역)
            where = whole if topic.startswith("본문:") else sent
            if re.search(topic.removeprefix("본문:"), where) and re.search(wrong, sent):
                out.append(f"확인된 틀린 문구: '{sent[:50]}' — {fix}")
        for since, wrong, ok, fix in DATED_WRONG:
            if today >= since and re.search(wrong, sent) and not re.search(ok, sent):
                out.append(f"시행일 지나 바뀐 말: '{sent[:50]}' — {fix}")
    return list(dict.fromkeys(out))


BALANCED_TAGS = ("p", "ul", "ol", "li", "table", "tr", "td", "th", "h2", "h3", "strong", "a")


_ANSWER_VERB = {"하나요": "해요", "되나요": "돼요", "있나요": "있어요", "없나요": "없어요", "받나요": "받아요", "내나요": "내요"}


def faq_answer_flip(body_html: str) -> list[str]:
    """자주 묻는 질문의 '아니요'가 바로 뒤 문장과 거꾸로인 것 (11/11 #1143: 질문을 '안 해도 되나요?'→'매달 신고해야 하나요?'로
    바꾸고 답의 '아니요'는 그대로 — '아니요. 전자카드 현장도 매달 … 해야 해요'). 질문 끝 말이 답 첫 문장에 그대로 긍정으로 있으면 막음."""
    out = []
    for q_html, a_html in re.findall(r"<h3[^>]*>(.*?)</h3>\s*<p[^>]*>(.*?)</p>", body_html or "", flags=re.S):
        q, a = _plain(q_html).strip(), _plain(a_html).strip()
        words = q.rstrip("?").split()
        if not q.endswith("?") or len(words) < 2 or not re.match(r"아니(요|에요)", a):
            continue
        verb = _ANSWER_VERB.get(words[-1])
        first = re.split(r"(?<=[.!?])\s+", re.sub(r"^아니(요|에요)[.,]?\s*", "", a))[0]
        shared = {w for w in words[:-2] if len(w) >= 2} & set(first.split())
        if verb and f"{words[-2][-2:]} {verb}" in first and len(shared) >= 2:
            out.append(f"자주 묻는 질문 답이 거꾸로: '{q[:40]}' → '아니요' 뒤에 '{first[:40]}' — '네'로 고치거나 질문을 바꾸기")
    return out


def html_unbalanced(body_html: str) -> list[str]:
    """여는 태그와 닫는 태그 수가 다른 것 (11/10 #2295: 목록 뒤에 짝 없는 '</li></ul>' — 화면에서 목록·문단이 깨질 수 있음)."""
    html = re.sub(r"<!--.*?-->", "", body_html or "", flags=re.S)
    out = []
    for t in BALANCED_TAGS:
        opened = len(re.findall(rf"<{t}(?:\s[^>]*)?>", html, re.I))
        closed = len(re.findall(rf"</{t}\s*>", html, re.I))
        if opened != closed:
            out.append(f"HTML 태그 짝이 안 맞음: <{t}> {opened}개, </{t}> {closed}개")
    return out


# --- +@ 보강 끼워 넣기 검사 (automation/plus_check.py 가 씀) ---
REFERENT = re.compile(r"위 (?:예시|예|표|계산|사례)|^이 (?:예시|사례|계산)|^[^.?!]{0,80}(?:도 같아요|도 마찬가지예요)\.|^다만 ")  # 앞 문단을 가리키는 말 ('다만'으로 시작하는 문단은 11/11 #2177·#3262·#1026 — 예시 문단이 끼어 '다만'이 엉뚱한 문장에 붙음; '이때·그러면'은 대개 같은 문단 안을 가리켜 뺌 — 보강 글 130편 대조; '…도 같아요'는 11/11 #3118 이직 문단이 끼어 '사업 양도로 넘어가도 같아요'가 신청서 얘기를 가리키게 됨)
_UNIT = re.compile(r"<(p|li|tr|h2|h3|h4)[^>]*>(.*?)</\1>", re.S)


def _units(html: str) -> list[str]:
    return [t for t in (_plain(x) for _, x in _UNIT.findall(html or "")) if t]


_SELF_Q = re.compile(r"[^.?!]{2,}\?(?![\"'”’」』])")


def body_self_question(old_html: str, new_html: str) -> list[str]:
    """새로 넣은 본문 문단(p·li)이 스스로 묻고 답하는 문장을 품으면 막는다. 질문은 소제목(h3)으로.
    (11/11 #3080: 상위 글 질문 검사를 통과하려고 '상담은 어디로 하면 될까요? 국번 없이 …' 처럼 본문에 질문을 끼워 넣음)"""
    old = set(_units(old_html))
    out = []
    for tag, inner in _UNIT.findall(new_html or ""):
        t = _plain(inner)
        if tag in ("p", "li") and t and t not in old and _SELF_Q.search(t):
            out.append(f"새 본문 문단 안에 질문 문장: '{t[:30]}…' — 질문은 소제목(h3)으로 두고 문단은 답만")
    return out


def referent_moved(old_html: str, new_html: str) -> list[str]:
    """'위 예시라면 …' 처럼 바로 앞 문단을 가리키는 원래 문장 앞에 새 문단이 끼면 가리키는 대상이 바뀜
    (11/11 #1802: 자녀 공제 예시가 '본인 1명 74,350원' 뒤에 끼어 '위 예시라면 80%는 59,480원'이 자녀 예시를 가리키게 됨)."""
    old, new = _units(old_html), _units(new_html)
    before = {t: old[i - 1] for i, t in enumerate(old) if i and REFERENT.search(t)}
    out = []
    for i, t in enumerate(new):
        if t in before and i and not new[i - 1].startswith(before[t][:25]):  # 앞 문단 끝에 문장을 덧붙인 건 괜찮음
            out.append(f"앞 문단을 가리키는 원래 문장 앞에 새 문단이 끼어 뜻이 바뀜: '{t[:30]}…' — 새 문단은 이 문장 뒤로 옮기기")
    return out
