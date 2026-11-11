"""초안 저장 관문: 글쓰기 세션의 독립 검수 기록을 확인해, 통과한 글만 공개 대기열에 넣는다.

글은 초안 단계에서 완성돼 있어야 한다 (공개는 시간이 되면 내보내기만 함).
검수 기록(queue JSON 의 "review")은 주장마다:
  {"claim": "글의 주장 요지", "urls": ["확인한 주소", ...], "verdict": "맞음|틀림|확인불가", "action": "유지|수정|완화|삭제"}
통과 조건: 주장 5개 이상, 본문에 단정형으로 남긴 주장은 '맞음+유지' 또는 '틀림+수정'(공식 출처대로 고침)이어야 하고
공식 기관 주소 1개 이상,
제목·요약문의 숫자는 모두 '맞음'인 주장 안에 있어야 함.
완화·삭제한 주장은 본문에 단정형으로 없으므로 통과. 고친(action 수정) 주장은 검수자가 다시 확인했다는 "rechecked": true 가 있어야 한다. 기록이 없으면 검수 안 한 글로 본다.
제목·요약문 숫자의 근거가 특정 시·도 사이트뿐이면 제목·요약문에 그 지역을 밝혀야 한다
(11/5 #482 도시가스: 전북 공급규정만 보고 '전출은 3일 전'을 전국 기준처럼 제목에 씀).
기록은 세션이 쓰는 것이라 완벽하지 않다 → 목적은 '검수를 깜빡하고 넘어간 글'이 공개되지 않게 하는 것.
"""
from __future__ import annotations

import re
import urllib.parse

from pathlib import Path

from .sources import is_official

KEPT = ("유지", "수정")
HOLD_META = "_firstlab_hold"  # 관문에서 막힌 초안의 이유 (설정 페이지 '검수 보류 초안')
MIN_CLAIMS = 5  # 이보다 적으면 검수를 제대로 안 한 것으로 본다
# 본문 길이에 비례한 최소 주장 수: 공백 뺀 글자 70자당 1개 (보통 글 2,100~2,400자 → 30~34개).
# 11/4 외부 검수: 글쓰기 검수는 글 1편에 주장 11~26개만 확인, 외부 검수는 같은 글에서 40~60개 → 틀린 곳은 모두 확인 안 한 문장에서 나옴.
CHARS_PER_CLAIM = 70
_NUM = re.compile(r"\d[\d,.]*")
_LAW_ARTICLE = re.compile(r"제\d+조")
# 광역 시·도 누리집 주소 → 제목·요약에 밝혀야 할 지역 이름 (하위 주소 포함, 예: news.seoul.go.kr)
REGION_HOSTS = {
    "seoul.go.kr": ("서울",), "busan.go.kr": ("부산",), "daegu.go.kr": ("대구",), "incheon.go.kr": ("인천",),
    "gwangju.go.kr": ("광주",), "daejeon.go.kr": ("대전",), "ulsan.go.kr": ("울산",), "sejong.go.kr": ("세종",),
    "gg.go.kr": ("경기",), "gangwon.go.kr": ("강원",), "provin.gangwon.kr": ("강원",),
    "chungbuk.go.kr": ("충북", "충청북도"), "chungnam.go.kr": ("충남", "충청남도"),
    "jeonbuk.go.kr": ("전북", "전라북도"), "jeonnam.go.kr": ("전남", "전라남도"),
    "gb.go.kr": ("경북", "경상북도"), "gyeongnam.go.kr": ("경남", "경상남도"), "jeju.go.kr": ("제주",),
}


def _numbers(text: str) -> set[str]:
    """비교용 숫자 (쉼표 제거, 연도 제외 — 연도는 본문에서 '올해'처럼 쓰는 경우가 많음)."""
    nums = {n.replace(",", "").rstrip(".") for n in _NUM.findall(text or "")}
    return {n for n in nums if n and not re.fullmatch(r"(19|20)\d\d", n)}


_STRUCTURE = re.compile(r"\d+\s*(?:단계|가지)")  # '4단계', '5가지' = 글을 나눈 개수 (검수할 사실이 아님)


def _headline_numbers(text: str) -> set[str]:
    return _numbers(_STRUCTURE.sub("", text or ""))


def region_of(url: str) -> tuple[str, ...] | None:
    """시·도 누리집이면 그 지역 이름들, 아니면(법령·중앙부처·공단 등 전국 자료) None."""
    host = (re.sub(r"^https?://", "", url.strip()).split("/")[0].split(":")[0]).lower()
    for domain, names in REGION_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return names
    return None


def _regional_only(review: list[dict], headline: str) -> list[str]:
    """제목·요약 숫자 중 근거가 시·도 자료뿐인데 제목·요약에 그 지역이 없는 것."""
    out = []
    for n in sorted(_headline_numbers(headline)):
        urls = [str(u) for i in review if _ok(i) and n in _numbers(str(i.get("claim", "")))
                for u in i.get("urls", []) if is_official(str(u))]
        regions = [region_of(u) for u in urls]
        if urls and all(regions) and not any(name in headline for r in regions for name in r):
            out.append(f"제목·요약문 숫자 {n} 의 근거가 {regions[0][0]} 자료뿐 — 지역 기준이면 제목·요약문에 지역을 밝히거나 전국 공통 근거(법령 등) 확인")
    return out


def _ok(item: dict) -> bool:
    """본문에 남겨도 되는 주장: 맞아서 유지 / 틀려서 공식 출처대로 고침 (확인 못 한 것은 완화·삭제해야 함)."""
    pair = (str(item.get("verdict", "")).strip(), str(item.get("action", "")).strip())
    return pair in (("맞음", "유지"), ("맞음", "수정"), ("틀림", "수정"))


SECOND_ROUND = 2


def second_review_missing(review: list[dict] | None) -> list[str]:
    """새 글은 2차 독립 검수(고친 글을 새 하위 에이전트가 처음부터 다시 검수)를 거쳐야 함 — 그 검수자가 맞음으로 본 주장에 "round": 2.

    왜 (11/8 운영자 '두 개 다 해 봐' — 오류율 낮추기): 글쓰기 작업의 1차 독립 검수 뒤에도 외부 검수가 글당 2.6곳을 더 고침(최근 7일 200편).
    외부 검수가 사실상 2차 검수였고, 12/5 뒤엔 외부 검수가 없으므로 글쓰기 작업이 스스로 한 번 더 한다.
    공개 대기열 첫 저장(publish_queue)에서만 봄 — 보류 글 고치기(hold.py)는 보류 사유를 보고 고치므로 다시 안 막음(영영 보류 방지).
    """
    if any(isinstance(i, dict) and i.get("round") == SECOND_ROUND for i in review or []):
        return []
    return ['2차 독립 검수 기록 없음 — 고친 글을 새 하위 에이전트에게 처음부터 다시 검수시키고, 그 검수자가 맞음으로 본 주장에 "round": 2']


def min_claims(body_html: str) -> int:
    """본문 글자 수(태그 뺌)에 맞는 최소 검수 주장 수."""
    text = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", body_html or ""))
    return max(MIN_CLAIMS, len(text) // CHARS_PER_CLAIM)


# 근로기준법 중 상시 4명 이하 사업장에는 적용되지 않는 조문 (시행령 별표1에 없는 근로시간·휴가 조문) — 이 조문을 근거로 한 글은
# 제목·요약에 '5명(5인) 이상' 조건을 밝혀야 함 (11/5 #613 연장수당·#630 연차 두 번 요약에서 빠짐)
SMALL_EXCLUDED = {50, 51, 52, 53, 56, 57, 58, 59, 60, 61, 62}
_LABOR_ARTICLE = re.compile(r"근로기준법\s*제(\d+)조(?!의)")
_SMALL_WORDS = re.compile(r"[5５]\s*(?:명|인)|다섯\s*명|4\s*(?:명|인)\s*이하")


def _small_workplace(review: list[dict], headline: str) -> list[str]:
    cited = {int(n) for item in review for n in _LABOR_ARTICLE.findall(str(item.get("claim", "")))}
    hit = sorted(cited & SMALL_EXCLUDED)
    if hit and not _SMALL_WORDS.search(headline):
        return [f"근로기준법 제{hit[0]}조는 상시 4명 이하 사업장에 적용되지 않음 — 제목이나 요약에 '상시 5명 이상 사업장' 조건을 적기"]
    return []


REVIEWED = Path(__file__).resolve().parents[1] / "automation" / "reviewed.txt"
_CALC_TAG = re.compile(r'\[firstlab_calc id="([a-z0-9-]+)"\]')


def reviewed_calcs(path: Path = REVIEWED) -> set[str] | None:
    """검수 기록의 'calc:<이름>' (독립 검수를 마친 계산기). 기록 파일이 없으면 None (막지 않음)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    return {w[5:] for w in (ln.split()[0] for ln in lines if ln.strip() and not ln.startswith("#")) if w.startswith("calc:")}


def unreviewed_calcs(body_html: str, path: Path = REVIEWED) -> list[str]:
    """본문에 넣은 계산기가 독립 검수 전이면 공개하지 않는다 (11/5 운영자: 계산기는 검수 없이 바로 공개되고 있었음)."""
    done = reviewed_calcs(path)
    if done is None:
        return []
    return [f"본문 계산기 '{c}' 독립 검수 전 — 계산기 파일(defs·data·tests)을 독립 검수한 뒤 automation/reviewed.txt 에 'calc:{c}' 줄 추가"
            for c in sorted(set(_CALC_TAG.findall(body_html or ""))) if c not in done]


def problems(review: list[dict] | None, title: str = "", excerpt: str = "", body_html: str = "", law_fetcher=None) -> list[str]:
    """통과 못 한 이유 목록 (빈 목록이면 통과).
    제목·요약문의 숫자는 독자가 가장 먼저 믿는 값이라, 검수된(맞음) 주장 안에 있어야 한다."""
    if not review:
        return ["검수 기록 없음"]
    out = []
    need = min_claims(body_html)
    if len(review) < need:
        out.append(f"검수한 주장이 {len(review)}개뿐 (본문 길이 기준 최소 {need}개 — 글의 모든 사실 문장을 검수)")
    verified = " ".join(str(i.get("claim", "")) for i in review if _ok(i))
    missing = (_headline_numbers(title) | _headline_numbers(excerpt)) - _numbers(verified)
    if missing:
        out.append(f"제목·요약문 숫자 {', '.join(sorted(missing))} 가 검수된 주장에 없음")
    out += unreviewed_calcs(body_html)
    out += _regional_only(review, f"{title} {excerpt}")
    out += _small_workplace(review, f"{title} {excerpt}")
    # 조건 누락 (11/5 운영자 '막을 대책 세워서 어떻게든 해야지') — bot/condition_gate.py
    from . import condition_gate, expiry
    out += condition_gate.headline_scope(title, excerpt, body_html)
    out += condition_gate.lead_vs_body(body_html)
    out += condition_gate.excerpt_vs_exclusions(excerpt, body_html)
    out += condition_gate.lead_thresholds(excerpt, body_html)
    out += condition_gate.title_qualifier(title, body_html)
    out += condition_gate.title_tier_amount(title, excerpt, body_html)
    out += condition_gate.excerpt_number_limits(excerpt, body_html)
    out += condition_gate.excerpt_principle(excerpt, body_html)
    out += condition_gate.heading_without_amounts(body_html)
    out += condition_gate.known_wrong(body_html)
    out += condition_gate.html_unbalanced(body_html)
    out += condition_gate.faq_answer_flip(body_html)
    from . import law_api
    if law_api.available():  # 법제처를 쓸 수 있는 서버에서만 (검사 환경은 건너뜀)
        out += condition_gate.unknown_law_names(body_html)
    out += condition_gate.title_length(title)
    out += condition_gate.calc_coverage(body_html)
    out += condition_gate.lead_minimums(excerpt, body_html)
    out += condition_gate.variant_only(body_html)
    out += condition_gate.law_absence(body_html)
    out += condition_gate.excerpt_apply_date(excerpt, body_html)
    out += condition_gate.exception_articles(review, law_fetcher)
    out += condition_gate.article_provisos(review, law_fetcher)
    out += condition_gate.facility_ordinance(title, review, excerpt, body_html)
    out += condition_gate.upcoming_amendments(review, body_html, None if law_fetcher is None else (lambda *a: []))
    out += condition_gate.ordinance_delegation(title, excerpt, body_html, review, law_fetcher,
                                               None if law_fetcher is None else (lambda *a: []))
    out += condition_gate.transition_rules(review, body_html)
    out += condition_gate.retention_start(body_html)
    out += condition_gate.approx_rounding(body_html)
    out += condition_gate.ordinary_wage_rule(body_html)
    out += condition_gate.small_workplace_items(body_html)
    out += condition_gate.headline_from_price(title, excerpt, body_html)
    out += expiry.round_headline(title, excerpt, " ".join(expiry.blocks(body_html)))
    out += condition_gate.holiday_attendance(excerpt, body_html)
    out += condition_gate.labor_day_exception(body_html)
    out += condition_gate.notice_year_label(title, excerpt, body_html)
    out += condition_gate.unresolved_delegation(body_html)
    out += condition_gate.county_scope(body_html)
    out += condition_gate.average_wage_floor(body_html)
    out += condition_gate.year_only_headline(title, excerpt, body_html)
    out += condition_gate.leave_promotion_extinction(body_html)
    out += condition_gate.readability(body_html)
    from . import plain_style  # 쉬운 말 모양·문장 길이 (11/10 운영자 '새로 적거나 수정하면 새로운 방식대로')
    out += plain_style.problems(body_html)
    out += condition_gate.stale_forms(review, None if law_fetcher is None else (lambda *a: []))
    for item in review:
        if str(item.get("action", "")).strip() not in KEPT:
            continue  # 완화·삭제: 본문에 단정형으로 남지 않음
        claim = str(item.get("claim", ""))[:30]
        verdict = str(item.get("verdict", "")).strip()
        if not _ok(item):
            out.append(f"'{claim}' 판정 {verdict or '없음'}인데 {item.get('action')}")
        elif any("law.go.kr" in str(u) for u in item.get("urls", [])) and not _LAW_ARTICLE.search(
                str(item.get("claim", "")) + " " + " ".join(urllib.parse.unquote(str(u)) for u in item.get("urls", []))):
            # 법령 근거는 조문 번호가 있어야 그 조문의 예외·단서·개정을 코드가 찾아 보여 줄 수 있다 (11/5 예외 누락 재발 방지)
            out.append(f"'{claim}' 법령 근거인데 조문 번호 없음 (주장에 '법령명 제N조' 적기)")
        elif str(item.get("action", "")).strip() == "수정" and item.get("rechecked") is not True:
            # 고친 문장은 독립 검수자가 다시 확인한 뒤에만 맞음 (11/5 재확인 결과가 오기 전에 올려 지적이 안 고쳐진 일)
            out.append(f"'{claim}' 고친 주장인데 검수자 재확인 기록(rechecked: true) 없음")
        elif not any(is_official(str(u)) for u in item.get("urls", [])):
            out.append(f"'{claim}' 공식 기관 출처 없음")
    return out
