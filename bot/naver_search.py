"""네이버 검색 API - 블로그 문서 수 조회 (키워드 경쟁도 계산용).

사주데이 애플리케이션 키를 공유하므로 호출 수를 반드시 제한해서 쓴다.
문서: https://developers.naver.com/docs/serviceapi/search/blog/blog.md
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BLOG_URL = "https://openapi.naver.com/v1/search/blog.json"


class CallLimitExceeded(RuntimeError):
    pass


class NaverSearchError(RuntimeError):
    """네이버가 돌려준 오류 코드/메시지를 그대로 담는다."""


# 자주 나오는 오류의 원인 안내
_HINTS = {
    401: "Client ID/Secret 이 틀렸거나 서로 다른 애플리케이션의 값입니다. 다시 입력하세요.",
    403: "이 애플리케이션에 '검색' API 가 추가되어 있지 않습니다. (내 애플리케이션 → API 설정)",
    429: "하루 호출 한도를 초과했습니다. 내일 다시 시도하세요.",
}


class NaverSearchClient:
    def __init__(self, client_id: str, client_secret: str, max_calls: int = 30):
        self.client_id = client_id
        self.client_secret = client_secret
        self.max_calls = max_calls
        self.calls = 0

    def blog_total(self, keyword: str) -> int:
        """키워드로 검색되는 네이버 블로그 글 수."""
        if self.calls >= self.max_calls:
            raise CallLimitExceeded(f"이번 실행의 호출 한도({self.max_calls}회)에 도달")
        self.calls += 1
        query = urllib.parse.urlencode({"query": keyword, "display": 1})
        req = urllib.request.Request(
            f"{BLOG_URL}?{query}",
            headers={"X-Naver-Client-Id": self.client_id, "X-Naver-Client-Secret": self.client_secret},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return int(json.load(resp).get("total", 0))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            raise NaverSearchError(f"HTTP {e.code}: {body}\n→ {_HINTS.get(e.code, '')}") from None
