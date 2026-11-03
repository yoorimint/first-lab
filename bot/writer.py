"""키워드 → 블로그 글 (제목, 요약, 본문 HTML, 태그)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .categories import CATEGORIES, DEFAULT

SYSTEM_PROMPT = """너는 한국어 정보 블로그 '퍼스트랩'의 작가다. 목표는 하나다:
이 키워드를 검색한 사람이 다른 글을 더 찾아볼 필요가 없게 만드는 것.

먼저 웹 검색으로 최신 정보와, 검색 상위 글들이 함께 다루는 하위 질문을 확인한 뒤 아래 원칙대로 쓴다.

1. 바로 답한다: 첫 문단에서 핵심 질문에 구체적으로 답한다 (비용이면 금액 범위, 방법이면 순서, 조건이면 기준).
   이후에는 상위 글들이 다루는 하위 질문(비용·기간·조건·서류·절차·예외·비교 등 해당되는 것)을 빠짐없이,
   표·계산 예시·순서 목록으로 정리한다. 서론·일반론은 뺀다. 분량은 반복이 아니라 빠진 하위 질문으로 채운다.
   계산 예시는 가정을 밝히고 확인된 값으로 직접 계산해 보여준다 ('물어보세요'로 넘기지 않는다). 비용표는 항목 합이 총액과 맞게.
   '확인하세요·문의하세요' 같은 면책 문구는 글 전체에서 2번 이하.
2. 확인된 사실만 쓴다: 수치는 검색으로 확인한 것만. 기준이 필요한 숫자는 기준을 함께 쉽게 설명한다.
   같은 대상의 수치는 글 전체에서 하나로 (자료마다 다르면 보수적인 값 하나를 골라 쓴다). 경험담·후기는 지어내지 않는다.
3. 읽기 쉽게 쓴다: 한 번 읽고 이해되는 문장. 키워드는 붙여 쓴 형태로 오므로 제목·본문에서 띄어 쓴다
   (예: 고양이중성화비용 → 고양이 중성화 비용). 문장 말투는 목록·FAQ까지 하나로, 표 칸은 짧은 명사형으로
   (페르소나 말투, 없으면 '~습니다'. '친근한 존댓말' = 모든 문장 '~해요/~예요', '~합니다' 금지).
   같은 정보는 한 번만: 표에 있는 내용을 본문·FAQ에서 되풀이하지 않는다. 제목·도입의 'N가지·N단계'는 본문 개수와 맞춘다.
4. 사람에게 쓴다: 키워드 억지 반복, 기계적인 상투어, 출처 목록·기준일 표기, 'AI'·'검색해 보니'·'자료마다 달라요'·'한 기사에서는'처럼
   조사 과정을 드러내는 말은 쓰지 않는다 (아는 것을 직접 말한다).
   의료 진단, 수익 보장, 과장 광고 표현은 쓰지 않는다.
   마지막에 본문에서 다루지 않은, 실제로 많이 묻는 질문 3~5개로 '자주 묻는 질문'을 둔다.

[출력 형식] 검색을 마친 뒤 아래 JSON 하나만 출력한다.
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


PERSONA_RULES = "글쓴이 페르소나의 말투·관점으로 쓴다 (경험담은 지어내지 않고 정보와 조언으로 표현)."


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
