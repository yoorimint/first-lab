"""git 병합 도구: JSON 목록 파일(wordpress/patches.json)을 양쪽 항목 합치기로 (11/6 감시 세션과 글쓰기 작업이 동시에 패치를 더해 충돌·push 실패).

git 이 부름: python3 automation/merge_json_list.py %O %A %B  (조상, 우리 쪽=결과를 여기에 씀, 상대 쪽)
우리 쪽 순서를 그대로 두고, 상대 쪽에서 새로 생긴 항목을 뒤에 붙인다. 조상에서 지운 항목은 양쪽 다 지웠을 때만 빠진다.
JSON 이 아니면 1을 돌려 git 이 보통 충돌로 처리하게 둔다.
"""
from __future__ import annotations

import json
import sys


def merge(base: list, ours: list, theirs: list) -> list:
    key = lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False)  # noqa: E731
    have = {key(x) for x in ours}
    base_keys = {key(x) for x in base}
    return ours + [x for x in theirs if key(x) not in have and key(x) not in base_keys]


def main(argv: list[str]) -> int:
    try:
        base, ours, theirs = (json.load(open(p, encoding="utf-8")) for p in argv[1:4])
    except (OSError, ValueError):
        return 1
    if not all(isinstance(x, list) for x in (base, ours, theirs)):
        return 1
    raw = open(argv[2], encoding="utf-8").read()
    indent = 1 if raw.startswith("[\n {") else 2
    with open(argv[2], "w", encoding="utf-8") as f:
        f.write(json.dumps(merge(base, ours, theirs), ensure_ascii=False, indent=indent) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
