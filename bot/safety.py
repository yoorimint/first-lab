"""AI 안전 검사: 단어 목록으로 못 거르는 부적합 키워드를 AI가 판정."""
from __future__ import annotations

import json
import re

SYSTEM = """너는 정보 블로그의 글감 심사자다. 각 검색 키워드가 '사실 정보 블로그 글 한 편'의 주제로 적합한지 판정한다.

[부적합 - ok=false]
- 실존 인물·연예인·유명인, 특정 사건·사고·뉴스 속보
- 정치, 종교
- 의료 진단·치료·의약품, 성인·도박·불법
- 특정 장소·매장을 찾는 검색(예: ○○ 근처, ○○점), 특정 쇼핑몰·브랜드 로그인/고객센터 찾기
- 의미가 불분명하거나 글로 답할 수 없는 검색

[출력] JSON 배열만 출력: [{"keyword": "...", "ok": true, "reason": "판정 이유 또는 검색 의도 10자 내외"}]"""


def classify(llm, keywords: list[str]) -> dict[str, tuple[bool, str]]:
    """{키워드: (적합 여부, 이유)}. 응답에 없는 키워드는 결과에서 빠진다(다음에 다시 검사)."""
    raw = llm.generate(SYSTEM, "\n".join(keywords))
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    items = json.loads(text[text.find("["): text.rfind("]") + 1])
    wanted = set(keywords)
    return {
        str(it["keyword"]): (bool(it.get("ok")), str(it.get("reason", ""))[:50])
        for it in items if str(it.get("keyword")) in wanted
    }
