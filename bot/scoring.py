"""글감 점수. 가중치는 WEIGHTS 만 고치면 된다."""
from __future__ import annotations

import math

from .keyword_filter import is_question

WEIGHTS = {
    "volume": 1.0,      # log10(검색량): 1천=3점, 1만=4점
    "ad_comp": 1.0,     # 광고 경쟁도 높음=1, 중간=0.5 → 광고주가 많은 키워드 = 단가 기대
    "ad_clicks": 0.5,   # log10(1+광고클릭)
    "question": 1.5,    # 질문형(방법·신청·차이 등) → 글로 답하기 좋고 경쟁이 덜함
    "rising": 1.0,      # 이전 조회 대비 1.5배 이상 상승
}
_COMP = {"높음": 1.0, "중간": 0.5}


def score(keyword: str, total: int, ad_comp: str | None, ad_clicks: float, prev_total: int | None) -> float:
    s = WEIGHTS["volume"] * math.log10(max(total, 1))
    s += WEIGHTS["ad_comp"] * _COMP.get(ad_comp or "", 0.0)
    s += WEIGHTS["ad_clicks"] * math.log10(1 + max(ad_clicks, 0))
    if is_question(keyword):
        s += WEIGHTS["question"]
    if prev_total and total >= prev_total * 1.5:
        s += WEIGHTS["rising"]
    return round(s, 2)
