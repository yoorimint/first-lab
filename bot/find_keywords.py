"""발행할 만한 키워드 찾기 (네이버 검색량 기반).

  1) 네이버 검색광고 API로 연관 키워드 + 월간 검색량 수집
  2) 검색량 범위 / 금지어 필터
  3) 점수 = 질문형(롱테일) 가산 + 검색량  → 높은 순 출력
     (질문형 키워드는 대형 사이트와 경쟁이 덜하고, 글 한 편으로 답하기 좋다)
  4) (선택) 네이버 검색 API 키가 있으면 블로그 글 수로 경쟁도까지 계산

사용: python3 -m bot.find_keywords 다이어트 홈트 [--min 500] [--max 30000] [--top 30]
"""
import argparse

from .config import load_secrets, require
from .keyword_filter import is_allowed, is_question
from .naver_ads import NaverAdsClient
from .settings import load_settings
from .textfmt import ljust, rjust


def score(keyword: str, volume: int) -> float:
    """질문형이면 우선, 같은 그룹 안에서는 검색량 순."""
    return (1_000_000 if is_question(keyword) else 0) + volume


def main() -> None:
    parser = argparse.ArgumentParser(description="발행 후보 키워드 찾기")
    parser.add_argument("hints", nargs="*", help="힌트 키워드 (최대 5개, 생략 시 설정 페이지 값)")
    parser.add_argument("--min", type=int, help="최소 월간 검색량 (생략 시 설정 페이지 값)")
    parser.add_argument("--max", type=int, help="최대 월간 검색량 (생략 시 설정 페이지 값)")
    parser.add_argument("--top", type=int, default=30, help="출력 개수")
    args = parser.parse_args()

    settings = load_settings()
    hints = args.hints or settings.hint_keywords
    if not hints:
        raise SystemExit("힌트 키워드가 없습니다. 명령에 적거나 설정 페이지에 입력하세요.")
    min_v = args.min if args.min is not None else settings.min_volume
    max_v = args.max if args.max is not None else settings.max_volume

    secrets = load_secrets()
    ads = NaverAdsClient(*require(
        secrets, "NAVER_AD_CUSTOMER_ID", "NAVER_AD_ACCESS_LICENSE", "NAVER_AD_SECRET_KEY"
    ))

    stats = ads.keyword_stats(hints)
    candidates = sorted(
        (s for s in stats if min_v <= s.total <= max_v and is_allowed(s.keyword, settings.blocked_words)),
        key=lambda s: score(s.keyword, s.total), reverse=True,
    )
    print(f"연관 키워드 {len(stats)}개 → 조건 통과 {len(candidates)}개\n")

    print(ljust("키워드", 26) + rjust("월간검색", 10) + "  유형")
    for s in candidates[: args.top]:
        kind = "질문형" if is_question(s.keyword) else ""
        print(ljust(s.keyword, 26) + rjust(f"{s.total:,}", 10) + "  " + kind)


if __name__ == "__main__":
    main()
