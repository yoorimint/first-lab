"""자동 발행에 부적합한 키워드 거르기.

규칙은 아래 목록만 고치면 되도록 데이터로 분리했다.
"""
from __future__ import annotations

import json
from pathlib import Path

import re

# 의료·의약품: AI 오류 시 실제 피해 + 구글 YMYL 엄격 평가
MEDICAL = ["병원", "의원", "내과", "외과", "이비인후과", "피부과", "치과", "한의원", "약국",
           "위고비", "마운자로", "삭센다", "처방", "부작용", "복용", "주사", "수술", "질환",
           "치료", "증상", "통증", "질병", "진단", "염증", "건강검진", "ADHD", "adhd", "정신과",
           "우울증", "공황", "탈모약", "다이어트약"]
# 장소 찾기 검색: 블로그 글로 유입되지 않음
LOCAL = ["근처", "주변", "가까운", "맛집", "헬스장", "피트니스", "학원", "매장", "지점"]
# 쇼핑·브랜드: 쇼핑몰/공식 사이트가 상위 독점
SHOPPING = ["가격비교", "최저가", "쿠팡", "구매", "판매", "할인코드"]

# 경험 후기형: AI가 실제 경험 없이 쓰면 허위 후기가 됨 (구글·공정위 문제)
REVIEW = ["후기", "리뷰", "내돈내산", "솔직", "사용기", "체험"]
# 정치·종교: 애드센스 민감 주제 + 운영자 결정으로 제외
POLITICS_RELIGION = ["대통령", "국회", "정당", "선거", "투표", "탄핵", "민주당", "국민의힘",
                     "교회", "성당", "사찰", "불교", "기독교", "천주교", "이슬람"]  # 애매한 건 AI 안전 검사가 거름

# 추천·이벤트형: 상품 고르기·판촉 의도 → 정보 글로 답하기 어렵고 쇼핑·광고 페이지가 상위 (운영자 결정으로 제외)
PROMOTION = ["추천", "이벤트", "쿠폰", "프로모션", "경품", "사은품", "순위", "랭킹", "베스트"]

BLOCKED_WORDS = MEDICAL + LOCAL + SHOPPING + REVIEW + POLITICS_RELIGION + PROMOTION


def is_allowed(keyword: str, extra_blocked: list[str] | None = None) -> bool:
    """extra_blocked: 설정 페이지의 '추가 금지어'."""
    return not any(word in keyword for word in BLOCKED_WORDS + (extra_blocked or []))


# 질문형(롱테일) 신호: 정보를 찾는 검색 → 글 한 편으로 답하기 좋음
QUESTION_WORDS = ["방법", "하는법", "신청", "조건", "자격", "기간", "기준", "차이", "뜻", "의미",
                  "비용", "순서", "준비물", "계산", "정리", "주의사항", "서류", "기한", "되나요", "가능",
                  "절차", "요령", "발급", "조회", "가격", "요금", "시기", "주기", "원인", "증상", "해결"]


def is_question(keyword: str) -> bool:
    return any(word in keyword for word in QUESTION_WORDS)


# 브랜드·회사 이름: 가격·요금이 자주 바뀌고 공식·가격비교 사이트가 상위를 차지하기 쉬움 → 감점 (scoring.py)
# 소문자로 비교한다 (KT → kt). 일반 단어와 겹치는 짧은 이름(국민, 우리 등)은 넣지 않는다.
BRANDS = [
    # 통신
    "skt", "sk텔레콤", "sk알뜰", "sk7", "kt", "lg", "유플러스", "헬로모바일", "리브엠", "토스모바일",
    "u+", "세븐모바일", "프리티", "이야기모바일",
    # 보일러·가전·생활
    "귀뚜라미", "경동나비엔", "나비엔", "린나이", "대성쎌틱", "코웨이", "쿠쿠", "쿠첸", "위닉스", "청호나이스",
    "sk매직", "일룸", "한샘", "에이스침대", "시몬스", "까사미아",
    # 자동차·타이어
    "테슬라", "현대차", "기아", "제네시스", "bmw", "벤츠", "아우디", "볼보", "쉐보레", "르노", "kg모빌리티",
    "토요타", "렉서스", "혼다", "포르쉐", "금호타이어", "넥센", "한국타이어", "미쉐린", "피렐리", "브리지스톤",
    # 전자
    "삼성", "갤럭시", "애플", "아이폰", "아이패드", "맥북", "에어팟", "다이슨", "차이슨", "샤오미", "로보락",
    # 유통·패션·외식
    "유니클로", "자라", "나이키", "아디다스", "뉴발란스", "무신사", "다이소", "이마트", "코스트코", "홈플러스",
    "올리브영", "스타벅스", "메가커피", "이디야", "맥도날드", "버거킹", "배민", "배달의민족", "요기요",
    "롯데마트", "롯데월드", "에버랜드", "신세계", "현대백화점", "11번가", "지마켓", "g마켓", "옥션", "컬리",
    "투썸", "빽다방", "컴포즈", "파리바게뜨", "뚜레쥬르", "교촌", "bhc", "bbq", "도미노",
    # 여행·항공
    "대한항공", "아시아나", "제주항공", "진에어", "티웨이", "에어부산", "야놀자", "여기어때", "아고다",
    # 보험·증권
    "삼성화재", "현대해상", "db손해보험", "kb손해보험", "메리츠", "한화생명", "교보생명", "키움", "미래에셋",
    # 금융 회사
    "카카오뱅크", "토스뱅크", "케이뱅크", "신한", "현대카드", "삼성카드", "kb국민", "우리카드", "하나카드",
    "롯데카드", "농협",
]


# 글쓰기 세션이 '특정 브랜드·회사 상품'이라 건너뛰며 적은 브랜드 (publish_queue.skip_keywords) — 표에 없는 브랜드도 배워서 막음
LEARNED_BRANDS = Path("/var/lib/first-lab/bot/learned-brands.json")
MIN_BRAND = 3   # 'kb'·'db' 처럼 짧으면 다른 말에 섞여 오탐 → 3글자 이상만 배움


def learned_brands(path: Path | None = None) -> list[str]:
    try:
        return [str(b) for b in json.loads((path or LEARNED_BRANDS).read_text(encoding="utf-8"))]
    except (OSError, ValueError):
        return []


def learn_brands(brands, path: Path | None = None) -> list[str]:
    """새로 배운 브랜드 (정규화, 3글자 이상)."""
    path = path or LEARNED_BRANDS
    have = learned_brands(path)
    new = [b for b in dict.fromkeys(str(x).lower().replace(" ", "") for x in brands)
           if len(b) >= MIN_BRAND and b not in have and b not in BRANDS]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(have + new, ensure_ascii=False), encoding="utf-8")
        _learned_cache.clear()
    return new


_learned_cache: list[list[str]] = []


def is_brand(keyword: str) -> bool:
    k = keyword.lower().replace(" ", "")
    if not _learned_cache:
        _learned_cache.append(learned_brands())
    return any(b in k for b in BRANDS) or any(b in k for b in _learned_cache[0])


# 롱테일 조합 수식어: 큰 키워드 뒤에 붙여 실제 검색량이 있는 조합을 찾는다 (정보형 위주, 후기·추천·순위 같은 상품형은 제외)
MODIFIERS = [
    # 방법·절차
    "방법", "하는법", "하는방법", "절차", "순서", "과정", "요령", "팁", "주의사항", "체크리스트",
    # 신청·행정
    "신청", "신청방법", "신청기간", "신청자격", "신청서류", "접수", "발급", "재발급", "등록", "변경",
    "해지", "취소", "조회", "확인", "갱신", "연장",
    # 조건·대상
    "조건", "자격", "자격조건", "대상", "기준", "나이", "연령", "소득기준",
    # 돈
    "비용", "가격", "요금", "금액", "수수료", "지원금", "지원", "환급", "환급금", "계산", "계산기",
    "계산방법", "할인", "견적", "평균", "시세", "세금",
    # 시간
    "기간", "기한", "시기", "시간", "주기", "일정", "마감", "유효기간", "소요기간",
    # 비교·종류·뜻
    "차이", "차이점", "비교", "종류", "장단점", "장점", "단점", "뜻", "의미",
    # 준비
    "준비물", "준비", "필요서류", "서류", "리스트",
    # 문제 해결
    "원인", "해결", "해결방법", "주의점", "불이익", "과태료", "벌금",  # 증상·부작용은 의료 금지어라 제외
]


_YEAR_PREFIX = re.compile(r"^20\d\d")
_SUFFIXES = sorted(MODIFIERS, key=len, reverse=True)


def subject(norm_keyword: str) -> str:
    """키워드의 '주제' 부분: 앞의 연도와 끝의 수식어 하나를 뗀다.
    예) 자동차정기검사비용·자동차정기검사조회·2027자동차정기검사 → 자동차정기검사 (같은 글로 답할 수 있는 키워드 묶음)"""
    n = _YEAR_PREFIX.sub("", norm_keyword)
    for suffix in _SUFFIXES:
        if n.endswith(suffix) and len(n) - len(suffix) >= 3:
            return n[: -len(suffix)]
    return n
