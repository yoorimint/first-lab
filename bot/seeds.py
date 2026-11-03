"""키워드 창고를 넓힐 출발점(씨앗).

분야를 정해두지 않는다. 출발점은 아래 순서로 섞어서 쓴다.
  1) 구글 한국 급상승 검색어 (매일 바뀜)
  2) 창고에 이미 쌓인 키워드 중 아직 확장하지 않은 것 (눈덩이 방식 → 분야가 저절로 넓어짐)
  3) BOOTSTRAP_SEEDS: 창고가 거의 비어 있을 때만 쓰는 '시동용' 일상어
"""
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET

# 창고가 작을 때만 사용. 특정 분야 지정이 아니라 일상에서 흔히 검색하는 넓은 단어들.
BOOTSTRAP_SEEDS = [
    "방법", "신청", "조회", "계산", "추천", "비교", "준비물", "뜻", "차이", "정리",
    "세금", "보험", "지원금", "연금", "대출", "적금", "카드", "요금", "환불", "자격증",
    "자동차", "이사", "전세", "여행", "요리", "청소", "운동", "공부", "취업", "육아",
    "스마트폰", "컴퓨터", "엑셀", "앱", "택배", "날씨", "선물", "캠핑", "반려견", "영어",
]
BOOTSTRAP_UNTIL = 1000  # 창고 키워드가 이 수보다 적으면 시동용 씨앗을 섞음

TRENDS_RSS = "https://trends.google.com/trending/rss?geo=KR"


def google_trends_kr(limit: int = 20) -> list[str]:
    """구글 한국 급상승 검색어. 실패하면 빈 목록 (봇은 계속 동작)."""
    try:
        with urllib.request.urlopen(TRENDS_RSS, timeout=15) as resp:
            root = ET.fromstring(resp.read())
        return [t.text.strip() for t in root.iter("title") if t.text][1:limit + 1]  # 첫 title 은 채널명
    except Exception:  # noqa: BLE001
        return []


if __name__ == "__main__":
    print("급상승 검색어:", google_trends_kr(10))
