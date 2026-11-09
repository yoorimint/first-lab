"""네이버 검색광고 API - 키워드 도구 (월간 검색량 조회).

문서: https://naver.github.io/searchad-apidoc/
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

BASE_URL = "https://api.searchad.naver.com"


@dataclass(frozen=True)
class KeywordStat:
    keyword: str
    pc: int
    mobile: int
    ad_competition: str  # 광고 경쟁도(낮음/중간/높음) - 블로그 경쟁도와는 다름
    ad_clicks: float = 0.0  # 월평균 광고 클릭 수(PC+모바일) - 광고주 관심도 = 애드센스 단가 추정 지표

    @property
    def total(self) -> int:
        return self.pc + self.mobile


def _to_int(value) -> int:
    """API는 검색량이 적으면 '< 10' 문자열을 준다 → 5로 간주."""
    return int(_to_float(value, small=5))


def _to_float(value, small: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.startswith("<"):
        return small
    try:
        return float(text)
    except ValueError:
        return 0.0


class NaverAdsClient:
    def __init__(self, customer_id: str, access_license: str, secret_key: str):
        self.customer_id = customer_id
        self.access_license = access_license
        self.secret_key = secret_key

    def _headers(self, method: str, uri: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}.{method}.{uri}".encode()
        signature = base64.b64encode(
            hmac.new(self.secret_key.encode(), message, hashlib.sha256).digest()
        ).decode()
        return {
            "X-Timestamp": timestamp,
            "X-API-KEY": self.access_license,
            "X-Customer": str(self.customer_id),
            "X-Signature": signature,
        }

    def keyword_stats(self, hints: list[str]) -> list[KeywordStat]:
        """힌트 키워드(최대 5개)와 연관 키워드들의 월간 검색량."""
        uri = "/keywordstool"
        # API는 힌트 키워드의 공백을 허용하지 않는다
        query = urllib.parse.urlencode(
            {"hintKeywords": ",".join(h.replace(" ", "") for h in hints[:5]), "showDetail": 1}
        )
        req = urllib.request.Request(f"{BASE_URL}{uri}?{query}", headers=self._headers("GET", uri))
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
        return [
            KeywordStat(
                keyword=item["relKeyword"],
                pc=_to_int(item["monthlyPcQcCnt"]),
                mobile=_to_int(item["monthlyMobileQcCnt"]),
                ad_competition=item.get("compIdx", ""),
                ad_clicks=_to_float(item.get("monthlyAvePcClkCnt")) + _to_float(item.get("monthlyAveMobileClkCnt")),
            )
            for item in data.get("keywordList", [])
        ]


def ads_client() -> NaverAdsClient:
    """서버 secrets.env 의 검색광고 키로 클라이언트 생성."""
    from .config import load_secrets, require

    return NaverAdsClient(*require(
        load_secrets(), "NAVER_AD_CUSTOMER_ID", "NAVER_AD_ACCESS_LICENSE", "NAVER_AD_SECRET_KEY"
    ))


def diagnose(client: NaverAdsClient | None = None) -> dict:
    """키워드 도구 연결 점검 (11/9: 403 으로 11/7 무렵부터 수집이 멈춤 — 운영자 '기존 키와 같은지 알아야').
    공개 배포 기록에는 정상/실패와 네이버 오류 문구만, 키 끝 4자리는 비공개 봇 상태(state)에만 남긴다."""
    import urllib.error
    client = client or ads_client()
    out = {"license_tail": client.access_license[-4:], "customer_id_tail": str(client.customer_id)[-3:],
           "secret_len": len(client.secret_key)}
    try:
        client.keyword_stats(["다이어트"])
        out.update(ok=True, status=200, message="")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        out.update(ok=False, status=e.code, message=body)
    except Exception as e:  # noqa: BLE001 - 연결 문제도 기록
        out.update(ok=False, status=0, message=f"{type(e).__name__}: {str(e)[:150]}")
    return out


if __name__ == "__main__":  # python3 -m bot.naver_ads — 서버 점검: 결과는 state 에, 화면에는 키 일부도 안 찍음
    from datetime import datetime
    from .state import load_state, save_state
    result = diagnose()
    result["checked"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    state = load_state()
    state["naver_ads_check"] = result
    save_state(state)
    print(f"네이버 검색광고 API: {'정상' if result['ok'] else '실패'} (HTTP {result['status']}) {result['message'][:200]}")
