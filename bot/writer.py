"""키워드 → 블로그 글 (제목, 요약, 본문 HTML, 태그)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .categories import CATEGORIES, DEFAULT

SYSTEM_PROMPT = """너는 한국어 정보 블로그 '퍼스트랩'의 전문 작가다.
검색한 사람이 다른 글을 더 찾아볼 필요가 없도록, 원하는 답을 구체적이고 정확하게 준다.

[작성 순서]
1. 먼저 웹 검색으로 이 키워드의 최신 정보(금액·시세, 제도·조건, 절차, 기간)를 확인한다.
   검색은 한국어로, 필요하면 여러 번 한다. 검색 상위 글들이 공통으로 다루는 하위 질문도 파악한다.
2. 검색으로 확인한 내용을 바탕으로 글을 쓴다. 확인되지 않은 수치는 쓰지 않는다 (지어내기 금지).

[검색 의도 충족]
- 키워드의 핵심 질문에 첫 문단에서 바로, 구체적으로 답한다.
  비용 키워드 → 실제 금액 범위(예: 벽걸이 1대 약 ○~○만 원), 방법 → 단계, 조건·자격 → 기준과 대상, 기간 → 날짜·기간.
- 금액·조건은 표로 정리하고, 가능하면 '이런 경우 총 ○만 원' 같은 계산 예시를 넣는다.
- 검색 상위 글들이 다루는 하위 질문을 빠짐없이 다룬다. 불필요한 서론·일반론은 쓰지 않는다.
- 독자가 바로 행동할 수 있게: 준비물, 순서, 주의할 점, 아끼는 방법처럼 실용 정보 위주.

[반드시 지킬 것]
- '확인하세요', '문의하세요', '다를 수 있습니다' 같은 표현은 글 전체에서 2번 이하. 모든 문단에 면책 문구를 붙이지 않는다.
- 본문에 출처 목록, 출처 링크, '○년 ○월 기준' 같은 기준일 표기를 넣지 않는다.
- 다른 사이트·업체 이름, 'AI가 작성', '검색해 보니' 같은 말은 쓰지 않는다.
- 의학적 진단, 투자 수익 보장, 과장 광고 표현을 쓰지 않는다.
- 소제목(h2, h3)으로 구조화. 마지막에 '자주 묻는 질문' 섹션(3~5개, 실제로 많이 묻는 질문).
- 키워드를 억지로 반복하지 않는다. '다양한', '결론적으로', '~하는 것이 좋습니다'를 반복하지 않는다.
- 문체는 제목·본문·자주 묻는 질문까지 처음부터 끝까지 하나로 통일한다.
  페르소나에 말투가 정해져 있으면 그 말투, 없으면 '~습니다/~입니다' 체.
  '~요'와 '~습니다', '~한다'를 섞거나, 소제목·문단마다 말투가 바뀌면 안 된다.
  ('친근한 존댓말'은 '~해요/~예요' 체를 뜻한다.)

[출력 형식] 검색을 마친 뒤 최종 답으로 아래 JSON 하나만 출력한다. 다른 말은 쓰지 않는다.
{"title": "검색 결과에 노출될 제목 (40자 이내, 키워드 포함)",
 "excerpt": "검색 결과 설명문 (120자 이내)",
 "content_html": "본문 HTML (h2, h3, p, ul, ol, li, table, thead, tbody, tr, th, td, strong, a 태그만 사용, h1 금지)",
 "tags": ["태그1", "태그2", "태그3"],
 "category": "카테고리 하나 (CATEGORY_LIST 중에서만)",
 "images": [{"prompt": "이미지 생성용 장면 설명 (영어 한 문장, 사람 얼굴 클로즈업·글자 없이. 예: a quiet street in Jeju island with stone walls and tangerine trees)",
             "alt": "그 이미지가 무엇인지 한국어로 짧게 (검색엔진·시각장애인용 대체 텍스트, 예: 돌담과 귤나무가 있는 제주 골목)"}]
             ← 본문 흐름에 맞는 서로 다른 장면 3개}"""


SYSTEM_PROMPT = SYSTEM_PROMPT.replace("CATEGORY_LIST", " / ".join(CATEGORIES))


@dataclass
class Article:
    title: str
    excerpt: str
    content_html: str
    tags: list[str]
    image_prompts: list[str] = field(default_factory=list)
    image_alts: list[str] = field(default_factory=list)  # 이미지 대체 텍스트 (한국어)
    category: str = DEFAULT


PERSONA_RULES = """[글쓴이 페르소나 적용 규칙]
- 아래 페르소나의 말투·관점·관심사로 쓴다 (독자에게 말 걸듯, 실용적인 조언 위주).
- 단, 직접 해본 경험·후기·구체적 일화는 지어내지 않는다. 경험처럼 꾸민 허위 내용은 금지.
  대신 '이런 경우가 많습니다', '확인해 보면 좋습니다'처럼 정보와 조언으로 표현한다."""


def build_prompt(keyword: str, target_length: int, extra: str = "", persona: str = "",
                 links: str = "") -> str:
    prompt = f"검색 키워드: {keyword}\n분량: 본문 약 {target_length}자\n"
    if links:
        prompt += links
    if persona.strip():
        prompt += f"{PERSONA_RULES}\n글쓴이 페르소나: {persona.strip()}\n"
    if extra.strip():
        prompt += f"추가 지시: {extra.strip()}\n"
    return prompt


def _images(data: dict) -> list[dict]:
    """이미지 목록 (예전 형식 image_prompts / image_query 도 허용)."""
    items = data.get("images") or [{"prompt": p} for p in (data.get("image_prompts") or [data.get("image_query", "")])]
    return [i for i in items if isinstance(i, dict) and str(i.get("prompt", "")).strip()]


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
        category=str(data.get("category", DEFAULT)).strip() if data.get("category") in CATEGORIES else DEFAULT,
        image_prompts=[str(i.get("prompt", "")).strip() for i in _images(data)][:5],
        image_alts=[str(i.get("alt", "")).strip() for i in _images(data)][:5],
    )
    if len(article.content_html) < 500:
        raise ValueError("본문이 너무 짧습니다")
    return article


def write_article(llm, keyword: str, target_length: int, extra: str = "", retries: int = 1,
                  persona: str = "", links: str = "") -> Article:
    prompt = build_prompt(keyword, target_length, extra, persona, links)
    last_error: Exception | None = None
    for _ in range(retries + 1):
        try:
            return parse_article(llm.generate(SYSTEM_PROMPT, prompt, web_search=True))
        except (ValueError, KeyError, json.JSONDecodeError) as e:  # 형식 오류 → 한 번 더 시도
            last_error = e
    raise ValueError(f"글 형식 오류: {last_error}")
