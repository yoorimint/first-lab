"""발행할 만한 키워드 찾기 (네이버 검색량 기반).

  1) 네이버 검색광고 API로 연관 키워드 + 월간 검색량 수집
  2) 검색량 범위 / 금지어 필터
  3) 점수 = 질문형(롱테일) 가산 + 검색량  → 높은 순
     (질문형 키워드는 대형 사이트와 경쟁이 덜하고, 글 한 편으로 답하기 좋다)

사용: python3 -m bot.find_keywords [힌트 ...] [--min 500] [--max 30000] [--top 30]
      힌트/범위를 생략하면 설정 페이지 값을 사용
"""
from __future__ import annotations

import argparse

from .config import load_secrets, require
from .keyword_filter import is_allowed, is_question
from .naver_ads import KeywordStat, NaverAdsClient
from .settings import load_settings
from .textfmt import ljust, rjust


def score(keyword: str, volume: int) -> float:
    """질문형이면 우선, 같은 그룹 안에서는 검색량 순."""
    return (1_000_000 if is_question(keyword) else 0) + volume


def ads_client() -> NaverAdsClient:
    return NaverAdsClient(*require(
        load_secrets(), "NAVER_AD_CUSTOMER_ID", "NAVER_AD_ACCESS_LICENSE", "NAVER_AD_SECRET_KEY"
    ))


def find_candidates(hints: list[str], min_v: int, max_v: int, blocked: list[str],
                    exclude: set[str] = frozenset()) -> tuple[int, list[KeywordStat]]:
    """(연관 키워드 수, 점수순 후보 목록). exclude: 이미 쓴 키워드."""
    stats = ads_client().keyword_stats(hints)
    candidates = sorted(
        (s for s in stats
         if min_v <= s.total <= max_v and is_allowed(s.keyword, blocked) and s.keyword not in exclude),
        key=lambda s: score(s.keyword, s.total), reverse=True,
    )
    return len(stats), candidates


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

    total, candidates = find_candidates(hints[:5], min_v, max_v, settings.blocked_words)
    print(f"연관 키워드 {total}개 → 조건 통과 {len(candidates)}개\n")
    print(ljust("키워드", 26) + rjust("월간검색", 10) + "  유형")
    for s in candidates[: args.top]:
        print(ljust(s.keyword, 26) + rjust(f"{s.total:,}", 10) + "  " + ("질문형" if is_question(s.keyword) else ""))


if __name__ == "__main__":
    main()
