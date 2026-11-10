"""쉬운 말 다듬기 최종 읽기용: 원래 글과 고친 글을 요소(문단·목록 항목·표 줄·소제목)마다 맞춰 바뀐 곳만 나란히 보여 준다.

11/10 운영자 '마지막에는 니가 한 번 더 기존 글하고 수정 글하고 처음부터 끝까지 읽어 보는 게 좋지 않나':
뜻 검수 뒤 감시 세션이 반영 전에 이 출력을 처음부터 끝까지 읽는다 (같은 문장은 빼서 읽을 양을 줄임).

사용: python3 automation/plain_diff.py <원본 JSON> <고친 HTML>
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

_ELEM = re.compile(r"<(p|li|h2|h3|tr)[^>]*>(.*?)</\1>", re.S)


def units(html: str) -> list[str]:
    body = (html or "").split("공식 안내 바로가기")[0]
    out = []
    for tag, inner in _ELEM.findall(body):
        inner = re.sub(r"<br\s*/?>", " / ", inner)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
        if text:
            out.append(f"[{tag}] {text}")
    return out


def diff(old_html: str, new_html: str) -> str:
    a, b = units(old_html), units(new_html)
    lines = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        for x in a[i1:i2]:
            lines.append(f"- {x}")
        for x in b[j1:j2]:
            lines.append(f"+ {x}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    old = json.loads(Path(argv[0]).read_text(encoding="utf-8"))["content_html"]
    new = Path(argv[1]).read_text(encoding="utf-8")
    print(diff(old, new))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
