"""발행 직전 교정: 편집자가 '원문 → 고친 문장' 쌍만 돌려주고, 코드가 정확히 일치하는 곳만 바꿔 끼운다.
글 전체를 다시 쓰게 하지 않으므로 출력 토큰(비용)이 적고, 구조·HTML 이 망가질 위험이 없다."""
from __future__ import annotations

import json
import re

SYSTEM = """너는 발행 직전 교정 편집자다. 아래 글을 실제 독자 입장에서 처음부터 끝까지 읽고, 독자가 걸릴 부분만 고친다.

고칠 것:
1. 앞뒤 불일치: 같은 대상의 수치·조건·개수(제목·도입의 'N가지' 포함)가 본문·표·자주 묻는 질문에서 다르면 하나로 맞춘다.
   어느 쪽이 맞는지는 글 안의 근거로 판단하고, 판단이 안 되면 범위로 쓴다. 계산 예시는 직접 다시 계산해 본다.
2. 어색한 문장: 조사·호응 오류, 번역투, 한 번 읽어 이해되지 않는 문장을 자연스럽게 고친다.
3. 반복: 앞에서 이미 말한 정보를 되풀이하는 문장은 지우거나 줄인다.
4. 말투: 글의 주된 말투 하나로 통일한다 (표 칸은 짧은 명사형).

지킬 것: 새로운 사실·수치를 추가하지 않는다. 제목·표·목록 구조는 그대로 둔다. 고칠 게 없으면 고치지 않는다.

출력: JSON 하나만. {"fixes": [{"before": "본문 HTML에 있는 원문 그대로(태그 포함 가능, 글 안에서 한 번만 나오는 길이로)", "after": "고친 내용 (지울 때는 빈 문자열)"}]}"""


def apply_fixes(html: str, fixes: list[dict]) -> tuple[str, int]:
    """글 안에서 정확히 한 번 나오는 원문만 바꾼다 (애매한 수정은 건너뜀). (새 HTML, 적용 수) 반환."""
    applied = 0
    for fix in fixes:
        before, after = str(fix.get("before") or ""), str(fix.get("after") or "")
        if before and html.count(before) == 1:
            html = html.replace(before, after)
            applied += 1
    return html, applied


def polish(llm, content_html: str) -> tuple[str, int]:
    raw = llm.generate(SYSTEM, f"[교정할 글 HTML]\n{content_html}")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    return apply_fixes(content_html, data.get("fixes") or [])
