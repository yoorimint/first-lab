"""자동 검수 (사실·이해·일관성): 완성된 글의 핵심 수치·사실을 웹 검색으로 다시 확인하고 틀린 부분만 고친다.

  - 틀린 수치 → 검색으로 확인한 값으로 수정 (문장·문체는 최대한 유지)
  - 확인 안 되는 주장이 많으면 → '검토 필요'로 표시 (자동 발행이어도 초안으로 남김)
글쓰기 AI 와 다른 호출(별도 검증자)이라, 처음 쓴 내용을 그대로 믿지 않고 다시 확인하는 효과.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

SYSTEM = """너는 한국어 정보 글의 검수 편집자다. 발행 전에 아래 네 가지만 점검하고, 문제가 있는 부분만 최소한으로 고친다.

1. 사실: 독자가 의사결정에 쓰는 핵심 주장(금액·비율·기간·조건·제도) 최대 8개를 웹 검색으로 확인.
   틀리면 확인한 값으로 고친다. 확인할 수 없는 수치는 검색으로 확인되는 값으로 바꾸고, 그래도 없으면 범위로 넓힌다.
   삭제는 최후 수단이며, 삭제 때문에 핵심 질문(비용·수익·조건 등)의 답이 빠지면 안 된다.
2. 이해: 기준 없이 나열된 숫자, 한 번 읽어 이해되지 않는 문장은 쉬운 설명으로 바꾸거나 삭제.
3. 일관성: 글 안에서 같은 대상의 수치가 서로 다르면(본문↔본문, 본문↔표) 근거가 확실한 하나로 맞추고,
   계산 예시는 그 값으로 다시 계산하고, 결론이 앞의 기준과 모순되지 않는지 본다. 표의 열·행 이름, 제목·도입의 'N가지·N단계'가
   실제 내용과 맞는지 확인한다. 말투 혼용과 '자료마다 달라요' 같은 조사 과정 언급도 바로잡는다.
4. 그 외 문장·구조·HTML·링크·이미지 태그는 그대로 둔다. 출처 표기나 기준일은 넣지 않는다.

최종 답은 JSON 하나만:
{"checks": [{"claim": "점검한 주장 요약", "verdict": "ok|fixed|removed|unverified"}],
 "edits": 사실 외에 고친 곳 수(이해·일관성),
 "content_html": "검수 반영된 본문 HTML 전체 (고칠 게 없으면 원문 그대로)"}"""

MAX_UNVERIFIED = 2  # 확인 못 한 주장이 이보다 많으면 사람 검토 필요


@dataclass
class FactCheck:
    content_html: str
    ok: int
    fixed: int
    removed: int
    unverified: int
    edits: int = 0  # 사실 외 수정(이해하기 어려운 문장, 말투·수치 불일치)
    unverified_claims: list[str] = field(default_factory=list)  # 확인 못 한 주장 (기록에 남겨 사람이 볼 수 있게)

    @property
    def needs_review(self) -> bool:
        return self.unverified > MAX_UNVERIFIED

    @property
    def summary(self) -> str:
        s = (f"검수: 사실 확인 {self.ok}·수정 {self.fixed}·삭제 {self.removed}·미확인 {self.unverified}, "
             f"문장 다듬기 {self.edits}")
        if self.unverified_claims:
            s += " [미확인: " + " / ".join(c[:60] for c in self.unverified_claims) + "]"
        return s + (" → 검토 필요" if self.needs_review else "")


def verify(llm, keyword: str, content_html: str) -> FactCheck:
    raw = llm.generate(SYSTEM, f"검색 키워드: {keyword}\n\n[검증할 글 HTML]\n{content_html}", web_search=True)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    checks = data.get("checks", [])
    verdicts = [str(c.get("verdict", "")) for c in checks]
    unverified = [str(c.get("claim", "")) for c in checks if str(c.get("verdict", "")) == "unverified"]
    html = str(data.get("content_html") or "").strip()
    if len(html) < len(content_html) * 0.7:  # 검증자가 글을 크게 줄여버리면 원문 유지
        html = content_html
    try:
        edits = int(data.get("edits") or 0)
    except (TypeError, ValueError):
        edits = 0
    return FactCheck(html, verdicts.count("ok"), verdicts.count("fixed"),
                     verdicts.count("removed"), verdicts.count("unverified"), edits, unverified)
