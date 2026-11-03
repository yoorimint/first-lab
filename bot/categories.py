"""블로그 카테고리 (운영자 확정 목록). 바꾸려면 이 목록만 고치면 배포 때 워드프레스에 반영된다."""

CATEGORIES = {  # 이름: 주소용 이름(slug)
    "생활정보": "life",
    "금융·세금": "finance",
    "자동차": "car",
    "부동산·이사": "housing",
    "여행": "travel",
    "반려동물": "pet",
    "교육·자격증": "education",
    "IT·디지털": "it",
    "취미·운동": "hobby",
    "가족·육아": "family",
}
DEFAULT = "생활정보"


def slug_of(name: str) -> str:
    return CATEGORIES.get(name.strip(), CATEGORIES[DEFAULT])


if __name__ == "__main__":  # wp-setup.sh 에서 사용: "이름<TAB>slug" 출력
    for name, slug in CATEGORIES.items():
        print(f"{name}\t{slug}")
