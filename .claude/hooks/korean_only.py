#!/usr/bin/env python3
"""운영자에게 보내는 마지막 답이 영어면 멈추지 않고 한글로 다시 쓰게 함 (CLAUDE.md '항상 한글로만', 11/11 운영자 '또 영어로 하노').

Stop 훅: 표준 입력 JSON 의 transcript_path 에서 마지막 사용자 메시지 뒤 어시스턴트 글을 모아
코드·주소·경로를 뺀 뒤 한글보다 영문 글자가 많으면 종료코드 2 (이유는 stderr → 다시 씀).
stop_hook_active 면(이미 한 번 고치게 했으면) 그냥 통과 — 무한 반복 방지.
"""
import json
import re
import sys

MIN_LATIN = 60       # 영문 글자가 이만큼은 있어야 판단 (짧은 답·명령어만 있는 답은 통과)
RATIO = 1.0          # 영문 글자 > 한글 글자 × RATIO 면 영어 답으로 봄


def last_reply(path: str) -> str:
    texts: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") or {}
            content = m.get("content")
            if m.get("role") == "user" and isinstance(content, str):
                texts = []  # 사람이 새로 말함 → 그 뒤 답만 봄
            elif m.get("role") == "user" and isinstance(content, list) and any(c.get("type") == "text" for c in content):
                texts = []
            elif m.get("role") == "assistant" and isinstance(content, list):
                texts += [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(texts)


def is_english(text: str) -> bool:
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"https?://\S+|[\w./-]+\.(?:py|md|php|json|html|txt|mjs|sh)\b|/[\w./-]+", " ", t)
    hangul = len(re.findall(r"[가-힣]", t))
    latin = len(re.findall(r"[A-Za-z]", t))
    return latin >= MIN_LATIN and latin > hangul * RATIO


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if data.get("stop_hook_active"):
        return 0
    path = data.get("transcript_path")
    if not path:
        return 0
    try:
        reply = last_reply(path)
    except OSError:
        return 0
    if is_english(reply):
        print("운영자에게는 항상 한글로만 말한다 (CLAUDE.md). 방금 답을 같은 내용으로 한글로 다시 써라.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
