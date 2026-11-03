"""키워드 검색량 조회 CLI.

사용: python3 -m bot.keywords 다이어트 [힌트2 ...] [--top 20]
"""
import argparse

from .config import load_secrets, require
from .naver_ads import NaverAdsClient


def main() -> None:
    parser = argparse.ArgumentParser(description="네이버 월간 검색량 조회")
    parser.add_argument("hints", nargs="+", help="힌트 키워드 (최대 5개)")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    secrets = load_secrets()
    client = NaverAdsClient(*require(
        secrets, "NAVER_AD_CUSTOMER_ID", "NAVER_AD_ACCESS_LICENSE", "NAVER_AD_SECRET_KEY"
    ))
    stats = sorted(client.keyword_stats(args.hints), key=lambda s: s.total, reverse=True)

    print(f"{'키워드':<24}{'월간 합계':>10}{'모바일':>10}{'PC':>9}  광고경쟁")
    for s in stats[: args.top]:
        print(f"{s.keyword:<24}{s.total:>10,}{s.mobile:>10,}{s.pc:>9,}  {s.ad_competition}")


if __name__ == "__main__":
    main()
