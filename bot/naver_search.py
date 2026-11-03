"""네이버 검색 API - 블로그 문서 수 조회 (키워드 경쟁도 계산용).

사주데이 애플리케이션 키를 공유하므로 호출 수를 반드시 제한해서 쓴다.
문서: https://developers.naver.com/docs/serviceapi/search/blog/blog.md
"""
import json
import urllib.parse
import urllib.request

BLOG_URL = "https://openapi.naver.com/v1/search/blog.json"


class CallLimitExceeded(RuntimeError):
    pass


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
        with urllib.request.urlopen(req, timeout=20) as resp:
            return int(json.load(resp).get("total", 0))
