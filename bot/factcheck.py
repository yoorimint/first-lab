"""자동 사실 검증: 완성된 글의 핵심 수치·사실을 웹 검색으로 다시 확인하고 틀린 부분만 고친다.

  - 틀린 수치 → 검색으로 확인한 값으로 수정 (문장·문체는 최대한 유지)
  - 확인 안 되는 주장이 많으면 → '검토 필요'로 표시 (자동 발행이어도 초안으로 남김)
글쓰기 AI 와 다른 호출(별도 검증자)이라, 처음 쓴 내용을 그대로 믿지 않고 다시 확인하는 효과.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

SYSTEM = """너는 한국어 정보 글의 사실 검증 편집자다.
1. 글에서 독자가 의사결정에 쓰는 핵심 주장(금액, 비율, 기간, 자격·조건, 제도 내용)을 최대 8개 고른다.
2. 각 주장을 웹 검색으로 확인한다.
3. 틀린 것은 확인한 값으로 고친다. 확인할 수 없는 구체적 수치는 범위로 넓히거나 삭제한다.
   기준이 불분명해 독자가 이해할 수 없는 수치(예: 무엇을 100으로 둔 지수인지 없는 숫자 나열)도 쉬운 설명으로 바꾸거나 삭제한다.
4. 고칠 곳 외에는 문장·문체·HTML 구조를 그대로 둔다. 출처 표기나 기준일은 넣지 않는다.

최종 답은 아래 JSON 하나만 출력한다.
{"checks": [{"claim": "주장 요약", "verdict": "ok|fixed|removed|unverified"}],
 "content_html": "수정 반영된 본문 HTML 전체 (고칠 게 없으면 원문 그대로)"}"""

MAX_UNVERIFIED = 2  # 확인 못 한 주장이 이보다 많으면 사람 검토 필요


@dataclass
class FactCheck:
    content_html: str
    ok: int
    fixed: int
    removed: int
    unverified: int

    @property
    def needs_review(self) -> bool:
        return self.unverified > MAX_UNVERIFIED

    @property
    def summary(self) -> str:
        s = f"사실검증 확인 {self.ok}·수정 {self.fixed}·삭제 {self.removed}·미확인 {self.unverified}"
        return s + (" → 검토 필요" if self.needs_review else "")


def verify(llm, keyword: str, content_html: str) -> FactCheck:
    raw = llm.generate(SYSTEM, f"검색 키워드: {keyword}\n\n[검증할 글 HTML]\n{content_html}", web_search=True)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    verdicts = [str(c.get("verdict", "")) for c in data.get("checks", [])]
    html = str(data.get("content_html") or "").strip()
    if len(html) < len(content_html) * 0.7:  # 검증자가 글을 크게 줄여버리면 원문 유지
        html = content_html
    return FactCheck(html, verdicts.count("ok"), verdicts.count("fixed"),
                     verdicts.count("removed"), verdicts.count("unverified"))
