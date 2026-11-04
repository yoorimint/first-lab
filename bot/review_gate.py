"""초안 저장 관문: 글쓰기 세션의 독립 검수 기록을 확인해, 통과한 글만 공개 대기열에 넣는다.

글은 초안 단계에서 완성돼 있어야 한다 (공개는 시간이 되면 내보내기만 함).
검수 기록(queue JSON 의 "review")은 주장마다:
  {"claim": "글의 주장 요지", "urls": ["확인한 주소", ...], "verdict": "맞음|틀림|확인불가", "action": "유지|수정|완화|삭제"}
통과 조건: 본문에 단정형으로 남긴 주장(유지·수정)은 모두 '맞음' + 공식 기관 주소 1개 이상.
완화·삭제한 주장은 본문에 단정형으로 없으므로 통과. 기록이 없으면 검수 안 한 글로 본다.
기록은 세션이 쓰는 것이라 완벽하지 않다 → 목적은 '검수를 깜빡하고 넘어간 글'이 공개되지 않게 하는 것.
"""
from __future__ import annotations

from .sources import is_official

KEPT = ("유지", "수정")


def problems(review: list[dict] | None) -> list[str]:
    """통과 못 한 이유 목록 (빈 목록이면 통과)."""
    if not review:
        return ["검수 기록 없음"]
    out = []
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
