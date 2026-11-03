"""글감을 찾을 출발점(씨앗 키워드).

  - DEFAULT_SEEDS: 힌트 키워드가 없을 때 쓰는 기본 주제 풀.
    글로 답하기 좋고(영상보다 글이 유리), 1년 내내 검색되는 생활 정보 위주.
    의료처럼 위험한 분야는 넣지 않는다. 분야를 늘리려면 목록에 추가만 하면 된다.
  - google_trends_kr(): 구글 한국 급상승 검색어 (설정에서 켰을 때만 사용)
"""
from __future__ import annotations

import random
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_SEEDS = {
    "금융·제도": ["연말정산", "종합소득세", "실업급여", "근로장려금", "국민연금", "건강보험료", "청년지원금",
              "주택청약", "적금", "신용점수", "환율", "부모급여", "기초연금"],
    "자동차": ["자동차보험", "자동차세", "운전면허", "중고차", "전기차 보조금", "타이어", "엔진오일", "과태료"],
    "부동산·주거": ["전세", "월세", "이사", "확정일자", "전입신고", "관리비", "도시가스", "전기요금"],
    "생활": ["여권", "주민등록증", "분리수거", "곰팡이 제거", "세탁", "청소", "정리정돈", "택배", "우체국"],
    "IT": ["엑셀", "카카오톡", "갤럭시", "아이폰", "윈도우", "와이파이", "유튜브", "PDF", "한글 문서"],
    "요리": ["레시피", "김치찌개", "된장찌개", "에어프라이어", "반찬", "도시락", "밀프렙"],
    "운동·취미": ["홈트", "스트레칭", "러닝", "등산", "캠핑", "자전거", "골프 입문"],
    "여행": ["해외여행 준비물", "항공권", "제주도 여행", "국내 여행지", "환전", "여행자보험"],
    "반려동물": ["강아지 훈련", "고양이 사료", "반려견 산책", "강아지 간식"],
    "교육·자격": ["자격증", "한국사능력검정시험", "토익", "맞춤법", "공무원 시험"],
}

TRENDS_RSS = "https://trends.google.com/trending/rss?geo=KR"


def random_seeds(count: int = 5) -> list[str]:
    """서로 다른 분야에서 하나씩 뽑아 주제가 한쪽으로 쏠리지 않게 한다."""
    categories = random.sample(list(DEFAULT_SEEDS), min(count, len(DEFAULT_SEEDS)))
    return [random.choice(DEFAULT_SEEDS[c]) for c in categories]


def google_trends_kr(limit: int = 20) -> list[str]:
    """구글 한국 급상승 검색어. 실패하면 빈 목록 (봇은 계속 동작)."""
    try:
        with urllib.request.urlopen(TRENDS_RSS, timeout=15) as resp:
            root = ET.fromstring(resp.read())
        return [t.text.strip() for t in root.iter("title") if t.text][1:limit + 1]  # 첫 title 은 채널명
    except Exception:  # noqa: BLE001
        return []


if __name__ == "__main__":
    print("기본 주제 예시:", random_seeds())
    print("급상승 검색어:", google_trends_kr(10))
