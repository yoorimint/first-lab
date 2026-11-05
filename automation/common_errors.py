"""'자주 틀린 유형' 확인표 만들기: 지금까지 고친 것(wordpress/patches.json)에서 유형별 건수와 최근 실제 예를 뽑아
automation/common-errors.md 로 쓴다. 글쓰기 세션의 독립 검수자가 이 표의 유형부터 확인한다 (automation/daily-writer.md 4-1).

왜: 같은 종류의 오류를 처음 쓸 때부터 안 내게 (11/5 운영자: '처음 글 적을 때 오류 없이').
고칠 때마다 다시 만들어 커밋한다 — 테스트가 파일이 최신인지 확인한다.

사용: python3 automation/common_errors.py
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "automation" / "common-errors.md"
SKIP = {"feature", "my_mistake"}  # 오류 아님 / 감시 세션 실수 (글쓰기와 무관)
EXAMPLES = 2


def build() -> str:
    reg = json.loads((ROOT / "automation" / "error_types.json").read_text(encoding="utf-8"))
    patches = json.loads((ROOT / "wordpress" / "patches.json").read_text(encoding="utf-8"))
    counts = Counter(p["type"] for p in patches if p["type"] not in SKIP)
    lines = ["# 자주 틀린 유형 (자동 생성: automation/common_errors.py — 직접 고치지 않음)", "",
             f"외부 검수에서 고친 {sum(counts.values())}곳을 유형별로 센 것. 많은 순. 검수할 때 이 유형부터 확인한다.", ""]
    for key, n in counts.most_common():
        t = reg[key]
        lines.append(f"## {t['name']} ({n}곳)")
        if t.get("cause"):
            lines.append(f"- 왜 틀렸나: {t['cause']}")
        lines.append(f"- 쓸 때: {t.get('writer_tip', '')}")
        recent = [p for p in reversed(patches) if p["type"] == key][:EXAMPLES]
        for p in recent:
            why = re.sub(r"^(11/\d+ )?(외부|독립) (재)?검수( 틀림| 확인불가)?(\([^)]*\))?:?\s*", "", p.get("why", ""))
            lines.append(f"  - 예 (글 {p['post']}): {why[:120]}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    OUT.write_text(build(), encoding="utf-8")
    print(f"저장: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
