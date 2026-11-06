"""글이 근거로 단 조문의 단서·예외 확인표 (글쓰기 세션이 올리기 전에): python3 -m bot.provisos 글.json

서버 관문(bot/condition_gate.article_provisos)이 같은 번호로 확인한다 — 검수 기록(review)에 단서마다
'<조문> 단서N 반영 — 본문 어디' 또는 '<조문> 단서N 해당 없음 — 이유' 항목이 없으면 보류 (11/6 운영자 '품질 1개 미만').
"""
from __future__ import annotations

import json
import sys

from .condition_gate import article_provisos, provisos_of


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("사용: python3 -m bot.provisos 글.json")
        return 2
    review = json.loads(open(argv[1], encoding="utf-8").read()).get("review") or []
    items = provisos_of(review)
    if not items:
        print("근거 조문의 단서 없음 (또는 조문을 못 읽음)")
        return 0
    for name, art, k, text in items:
        print(f"[{name} {art} 단서{k}] {text}")
    missing = article_provisos(review)
    print(f"\n확인 안 한 단서 {len(missing)}개" + ("" if not missing else " — 검수 기록(review)에 아래 꼴로 넣기 (urls 는 그 조문 주소):"))
    for name, art, k, _ in items:
        print(f'  {{"claim": "{name} {art} 단서{k} 반영 — <본문 어디에 썼는지> | 또는 해당 없음 — <이유>", '
              f'"verdict": "맞음", "action": "유지", "urls": ["https://www.law.go.kr/법령/{name}/{art}"]}}')
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
