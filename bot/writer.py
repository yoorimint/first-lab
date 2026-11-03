"""키워드 → 블로그 글 (제목, 요약, 본문 HTML, 태그)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

SYSTEM_PROMPT = """너는 한국어 정보 블로그 '퍼스트랩'의 전문 작가다.
검색한 사람이 원하는 답을 빠르고 정확하게 얻도록 글을 쓴다.

[반드시 지킬 것]
- 확실하지 않은 수치·날짜·금액·제도 내용은 지어내지 말고, "공식 기관에서 확인 필요"라고 쓴다.
- 의학적 진단, 투자 수익 보장, 과장 광고 표현을 쓰지 않는다.
- 첫 문단에서 검색 의도에 대한 핵심 답을 바로 준다.
- 소제목(h2, h3)으로 구조화하고, 비교·정리가 필요하면 표(table)를 쓴다.
- 마지막에 '자주 묻는 질문' 섹션(3~5개)을 넣는다.
- 키워드를 억지로 반복하지 않는다. 자연스러운 문장으로 쓴다.
- 다른 사이트 이름이나 'AI가 작성' 같은 말은 본문에 쓰지 않는다.

[출력 형식] 아래 JSON 하나만 출력한다. 다른 말은 쓰지 않는다.
{"title": "검색 결과에 노출될 제목 (40자 이내, 키워드 포함)",
 "excerpt": "검색 결과 설명문 (120자 이내)",
 "content_html": "본문 HTML (h2, h3, p, ul, ol, li, table, thead, tbody, tr, th, td, strong 태그만 사용, h1 금지)",
 "tags": ["태그1", "태그2", "태그3"],
 "image_query": "본문에 어울리는 이미지 설명 (영어 한 문장, 사람 얼굴 클로즈업·글자 없이. 예: a quiet street in Jeju island with stone walls and tangerine trees)"}"""


@dataclass
class Article:
    title: str
    excerpt: str
    content_html: str
    tags: list[str]
    image_query: str = ""


PERSONA_RULES = """[글쓴이 페르소나 적용 규칙]
- 아래 페르소나의 말투·관점·관심사로 쓴다 (독자에게 말 걸듯, 실용적인 조언 위주).
- 단, 직접 해본 경험·후기·구체적 일화는 지어내지 않는다. 경험처럼 꾸민 허위 내용은 금지.
  대신 '이런 경우가 많습니다', '확인해 보면 좋습니다'처럼 정보와 조언으로 표현한다."""


def build_prompt(keyword: str, target_length: int, extra: str = "", persona: str = "") -> str:
    prompt = f"검색 키워드: {keyword}\n분량: 본문 약 {target_length}자\n"
    if persona.strip():
        prompt += f"{PERSONA_RULES}\n글쓴이 페르소나: {persona.strip()}\n"
    if extra.strip():
        prompt += f"추가 지시: {extra.strip()}\n"
    return prompt


def parse_article(raw: str) -> Article:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)  # 코드블록으로 감싼 경우 제거
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start : end + 1])
    article = Article(
        title=str(data["title"]).strip(),
        excerpt=str(data.get("excerpt", "")).strip(),
        content_html=str(data["content_html"]).strip(),
        tags=[str(t).strip() for t in data.get("tags", []) if str(t).strip()][:8],
        image_query=str(data.get("image_query", "")).strip(),
    )
    if len(article.content_html) < 500:
        raise ValueError("본문이 너무 짧습니다")
    return article


def write_article(llm, keyword: str, target_length: int, extra: str = "", retries: int = 1,
                  persona: str = "") -> Article:
    prompt = build_prompt(keyword, target_length, extra, persona)
    last_error: Exception | None = None
    for _ in range(retries + 1):
        try:
            return parse_article(llm.generate(SYSTEM_PROMPT, prompt))
        except (ValueError, KeyError, json.JSONDecodeError) as e:  # 형식 오류 → 한 번 더 시도
            last_error = e
    raise ValueError(f"글 형식 오류: {last_error}")
