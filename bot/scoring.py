"""글감 점수. 가중치는 WEIGHTS 만 고치면 된다.

전략: 신규 사이트는 큰 키워드(헤드)로 대형 사이트를 이기기 어렵다.
→ 큰 키워드에 딸린 '구체적인 롱테일'을 우선한다.
   예) 헤드 '퇴직금'(월 20만) → 롱테일 '퇴직금계산1년미만'(월 2천)
"""
from __future__ import annotations

import math

from .keyword_filter import is_brand, is_question

WEIGHTS = {
    "volume": 1.0,       # log10(검색량), VOLUME_CAP 이상은 가산 없음
    "longtail": 2.0,     # 검색량이 훨씬 큰 헤드 키워드를 포함한 구체적 키워드
    "specific": 0.5,     # 글자 수가 긴(구체적인) 키워드
    "question": 1.0,     # 질문형(방법·신청·차이 등) → 글로 답하기 좋음
    "ad_comp": 0.0,      # 광고 경쟁도 (가산 안 함: 광고 경쟁이 높은 주제는 대형 사이트도 많아 후보가 그쪽으로 쏠렸음)
    "ad_clicks": 0.3,    # log10(1+광고클릭)
    "rising": 3.0,       # 이전 조회 대비 1.5배 이상 상승 (갑자기 는 수요는 아직 글이 적을 가능성이 큼)
    "head_penalty": -1.5,  # 롱테일이 아닌 큰 키워드 (대형 사이트와 정면 경쟁)
    "brand": -3.0,       # 브랜드·회사 이름 포함 (가격 변동 잦고 공식 사이트 우세) - 목록은 keyword_filter.BRANDS
}
VOLUME_CAP = 3000        # 이 이상 검색량은 점수에 더 반영하지 않음
LONGTAIL_MAX = 10000     # 이보다 크면 헤드를 포함해도 롱테일로 보지 않음
HEAD_VOLUME = 20000      # 이보다 크고 롱테일이 아니면 '헤드 키워드'로 보고 감점
HEAD_RATIO = 5           # 포함된 키워드의 검색량이 5배 이상이면 그게 '헤드'
SPECIFIC_LEN = 8         # 공백 제외 글자 수
_COMP = {"높음": 1.0, "중간": 0.5}


def find_head(norm: str, total: int, volumes: dict[str, int]) -> str | None:
    """키워드 안에 포함된 '훨씬 큰' 키워드(헤드). 긴 것부터 찾아 처음 맞는 것을 반환."""
    for size in range(len(norm) - 1, 1, -1):
        for i in range(len(norm) - size + 1):
            sub = norm[i:i + size]
            if volumes.get(sub, 0) >= total * HEAD_RATIO:
                return sub
    return None


def score(keyword: str, norm: str, total: int, ad_comp: str | None, ad_clicks: float,
          prev_total: int | None, head: str | None) -> float:
    s = WEIGHTS["volume"] * math.log10(min(max(total, 1), VOLUME_CAP))
    if head and total <= LONGTAIL_MAX:
        s += WEIGHTS["longtail"]
    elif total > HEAD_VOLUME:
        s += WEIGHTS["head_penalty"]
    if len(norm) >= SPECIFIC_LEN:
        s += WEIGHTS["specific"]
    if is_question(keyword):
        s += WEIGHTS["question"]
    s += WEIGHTS["ad_comp"] * _COMP.get(ad_comp or "", 0.0)
    s += WEIGHTS["ad_clicks"] * math.log10(1 + max(ad_clicks, 0))
    if prev_total and total >= prev_total * 1.5:
        s += WEIGHTS["rising"]
    if is_brand(keyword):
        s += WEIGHTS["brand"]
    return round(s, 2)
