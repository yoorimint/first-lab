"""발행할 만한 키워드 찾기: 검색량은 적당하고 경쟁(블로그 글 수)은 적은 키워드.

  1) 네이버 검색광고 API로 연관 키워드 + 월간 검색량 수집
  2) 검색량 범위 / 금지어 필터
  3) 검색량 많은 순으로 상위 N개만 블로그 글 수 조회 (호출 수 제한)
  4) 기회 점수 = 월간 검색량 ÷ 블로그 글 수  (높을수록 유리)

사용: python3 -m bot.find_keywords 다이어트 홈트 [--min 500] [--max 30000] [--check 30]
"""
import argparse

from .config import load_secrets, require
from .keyword_filter import is_allowed
from .naver_ads import NaverAdsClient
from .naver_search import CallLimitExceeded, NaverSearchClient, NaverSearchError
from .textfmt import ljust, rjust


def main() -> None:
    parser = argparse.ArgumentParser(description="저경쟁 키워드 찾기")
    parser.add_argument("hints", nargs="+", help="힌트 키워드 (최대 5개)")
    parser.add_argument("--min", type=int, default=500, help="최소 월간 검색량")
    parser.add_argument("--max", type=int, default=30000, help="최대 월간 검색량")
    parser.add_argument("--check", type=int, default=30, help="블로그 글 수를 조회할 키워드 수(=API 호출 수)")
    args = parser.parse_args()

    secrets = load_secrets()
    ads = NaverAdsClient(*require(
        secrets, "NAVER_AD_CUSTOMER_ID", "NAVER_AD_ACCESS_LICENSE", "NAVER_AD_SECRET_KEY"
    ))
    search = NaverSearchClient(
        *require(secrets, "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"), max_calls=args.check
    )

    stats = ads.keyword_stats(args.hints)
    candidates = sorted(
        (s for s in stats if args.min <= s.total <= args.max and is_allowed(s.keyword)),
        key=lambda s: s.total, reverse=True,
    )[: args.check]
    print(f"연관 키워드 {len(stats)}개 → 조건 통과 {len(candidates)}개 (블로그 글 수 조회)\n")

    rows = []
    for s in candidates:
        try:
            docs = search.blog_total(s.keyword)
        except CallLimitExceeded:
            break
        except NaverSearchError as e:
            raise SystemExit(f"네이버 검색 API 오류 - {e}")
        rows.append((s, docs, s.total / max(docs, 1)))
    rows.sort(key=lambda r: r[2], reverse=True)

    print(ljust("키워드", 24) + rjust("월간검색", 10) + rjust("블로그글수", 12) + rjust("기회점수", 10))
    for s, docs, score in rows:
        print(ljust(s.keyword, 24) + rjust(f"{s.total:,}", 10) + rjust(f"{docs:,}", 12) + rjust(f"{score:.3f}", 10))
    print(f"\n네이버 검색 API 호출: {search.calls}회")


if __name__ == "__main__":
    main()
