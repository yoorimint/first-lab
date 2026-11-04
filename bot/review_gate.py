"""초안 저장 관문: 글쓰기 세션의 독립 검수 기록을 확인해, 통과한 글만 공개 대기열에 넣는다.

글은 초안 단계에서 완성돼 있어야 한다 (공개는 시간이 되면 내보내기만 함).
검수 기록(queue JSON 의 "review")은 주장마다:
  {"claim": "글의 주장 요지", "urls": ["확인한 주소", ...], "verdict": "맞음|틀림|확인불가", "action": "유지|수정|완화|삭제"}
통과 조건: 주장 5개 이상, 본문에 단정형으로 남긴 주장(유지·수정)은 모두 '맞음' + 공식 기관 주소 1개 이상,
제목·요약문의 숫자는 모두 '맞음'인 주장 안에 있어야 함.
완화·삭제한 주장은 본문에 단정형으로 없으므로 통과. 기록이 없으면 검수 안 한 글로 본다.
기록은 세션이 쓰는 것이라 완벽하지 않다 → 목적은 '검수를 깜빡하고 넘어간 글'이 공개되지 않게 하는 것.
"""
from __future__ import annotations

import re

from .sources import is_official

KEPT = ("유지", "수정")
HOLD_META = "_firstlab_hold"  # 관문에서 막힌 초안의 이유 (설정 페이지 '검수 보류 초안')
MIN_CLAIMS = 5  # 이보다 적으면 검수를 제대로 안 한 것으로 본다
_NUM = re.compile(r"\d[\d,.]*")


def _numbers(text: str) -> set[str]:
    """비교용 숫자 (쉼표 제거, 연도 제외 — 연도는 본문에서 '올해'처럼 쓰는 경우가 많음)."""
    nums = {n.replace(",", "").rstrip(".") for n in _NUM.findall(text or "")}
    return {n for n in nums if n and not re.fullmatch(r"(19|20)\d\d", n)}


def problems(review: list[dict] | None, title: str = "", excerpt: str = "") -> list[str]:
    """통과 못 한 이유 목록 (빈 목록이면 통과).
    제목·요약문의 숫자는 독자가 가장 먼저 믿는 값이라, 검수된(맞음) 주장 안에 있어야 한다."""
    if not review:
        return ["검수 기록 없음"]
    out = []
    if len(review) < MIN_CLAIMS:
        out.append(f"검수한 주장이 {len(review)}개뿐 (최소 {MIN_CLAIMS}개)")
    verified = " ".join(str(i.get("claim", "")) for i in review
                        if str(i.get("action", "")).strip() in KEPT and str(i.get("verdict", "")).strip() == "맞음")
    missing = (_numbers(title) | _numbers(excerpt)) - _numbers(verified)
    if missing:
        out.append(f"제목·요약문 숫자 {', '.join(sorted(missing))} 가 검수된 주장에 없음")
    for item in review:
        if str(item.get("action", "")).strip() not in KEPT:
            continue  # 완화·삭제: 본문에 단정형으로 남지 않음
        claim = str(item.get("claim", ""))[:30]
        verdict = str(item.get("verdict", "")).strip()
        if verdict != "맞음":
            out.append(f"'{claim}' 판정 {verdict or '없음'}인데 그대로 씀")
        elif not any(is_official(str(u)) for u in item.get("urls", [])):
            out.append(f"'{claim}' 공식 기관 출처 없음")
    return out
