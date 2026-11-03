"""키워드 경쟁도: Gemini 의 '구글 검색 연동(grounding)'으로 실제 구글 상위 사이트를 보고 판단 (무료 한도 내).

상위에 정부·위키·대형 언론·대기업 사이트가 많으면 어려움, 개인 블로그가 많으면 해볼 만함.
주의: grounding 출처는 Gemini 가 검색 후 참고한 페이지라서 '구글 상위 10개'와 완전히 같지는 않다.

사용(점검): python -m bot.competition 퇴직금계산1년미만
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

GROUNDING_MODELS = ("gemini-2.5-flash",)  # 무료 grounding 제공 모델 (2.5-flash-lite 는 신규 사용자 불가 확인됨)

# 강한 경쟁자: 이런 사이트가 상위를 차지하면 신규 블로그가 이기기 어렵다
STRONG_SUFFIXES = (".go.kr", ".or.kr", ".ac.kr", ".gov", "wikipedia.org", "namu.wiki", "youtube.com")
STRONG_DOMAINS = {
    # 언론
    "chosun.com", "joongang.co.kr", "donga.com", "hani.co.kr", "khan.co.kr", "yna.co.kr", "mk.co.kr",
    "hankyung.com", "sedaily.com", "edaily.co.kr", "mt.co.kr", "news1.kr", "newsis.com", "sbs.co.kr",
    "kbs.co.kr", "imbc.com", "jtbc.co.kr", "ytn.co.kr", "heraldcorp.com", "asiae.co.kr", "fnnews.com",
    # 포털·대형 플랫폼
    "terms.naver.com", "kin.naver.com", "namu.wiki", "daum.net", "kakao.com", "coupang.com",
    "samsung.com", "lg.co.kr", "kbstar.com", "shinhan.com", "wooribank.com", "hanabank.com",
    "toss.im", "banksalad.com", "kakaobank.com", "11st.co.kr", "gmarket.co.kr",
    # 취업·부동산·생활 대형 서비스
    "saramin.co.kr", "jobkorea.co.kr", "incruit.com", "albamon.com", "zigbang.com", "dabangapp.com",
    "hometax.go.kr", "gov.kr", "bokjiro.go.kr",
}
# 약한 경쟁자: 개인 블로그 플랫폼
WEAK_SUFFIXES = ("tistory.com", "blogspot.com", "brunch.co.kr", "velog.io", "blog.naver.com",
                 "medium.com", "wordpress.com", "github.io")


@dataclass
class Competition:
    difficulty: float        # 0(쉬움) ~ 1(어려움)
    strong: int
    weak: int
    total: int
    domains: list[str]

    @property
    def summary(self) -> str:
        return f"상위 {self.total}곳 중 대형 {self.strong}·개인블로그 {self.weak}"


def classify_domain(domain: str) -> str:
    d = domain.lower().removeprefix("www.")
    if d in STRONG_DOMAINS or d.endswith(STRONG_SUFFIXES):
        return "strong"
    if d.endswith(WEAK_SUFFIXES):
        return "weak"
    return "medium"  # 알 수 없는 중소 사이트


def analyze(domains: list[str]) -> Competition:
    kinds = [classify_domain(d) for d in domains]
    n = len(kinds)
    strong, weak = kinds.count("strong"), kinds.count("weak")
    difficulty = (strong + 0.5 * (n - strong - weak)) / n if n else 0.5
    return Competition(round(difficulty, 2), strong, weak, n, domains)


def check(api_key: str, keyword: str) -> Competition:
    """구글 검색 연동으로 상위 출처 도메인을 모아 경쟁도를 계산."""
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=90_000))
    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
    prompt = f"구글에서 '{keyword}'를 검색했을 때 상위에 나오는 웹페이지들을 찾아서, 각 페이지의 사이트 이름만 짧게 나열해."
    last_error: Exception | None = None
    for model in GROUNDING_MODELS:
        try:
            response = client.models.generate_content(model=model, contents=prompt, config=config)
            meta = response.candidates[0].grounding_metadata if response.candidates else None
            chunks = (meta.grounding_chunks or []) if meta else []
            domains = [c.web.domain or c.web.title or "" for c in chunks if c.web]
            domains = list(dict.fromkeys(d.strip().lower() for d in domains if d and d.strip()))[:10]
            if not domains:
                raise RuntimeError("검색 출처가 비어 있습니다")
            return analyze(domains)
        except (errors.APIError, RuntimeError) as e:
            last_error = e
    raise RuntimeError(f"경쟁도 조회 실패: {last_error}")


if __name__ == "__main__":
    from .settings import load_settings

    s = load_settings()
    if not s.gemini_key:
        raise SystemExit("Gemini API 키가 없습니다 (설정 페이지)")
    for kw in sys.argv[1:] or ["퇴직금계산1년미만"]:
        c = check(s.gemini_key, kw)
        print(f"{kw}: 난이도 {c.difficulty} ({c.summary}) {c.domains}")
