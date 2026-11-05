"""법령 조문 원문 꺼내기 (글쓰기 세션·검수자용): python3 -m bot.lawtext 고용보험법 제46조 [제48조 ...]

law.go.kr 은 WebFetch 로 열면 빈 페이지가 오는 경우가 많아, 검수자가 원문을 못 읽고 '확인불가'로 남기는 일이 있었다
(11/5 #537: 수의사법 시행규칙·가축전염병 예방법에 그대로 있는 내용이 확인불가로 나옴). 껍데기 페이지에서 본문 주소를 찾아
조문 하나를 그대로 보여 주고, 그 조문의 개정·예외(단서) 문장도 함께 보여 준다.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import time
import xml.etree.ElementTree as ET

from . import law_api
from .source_check import article_text, exceptions_in, fetch, recent_changes

TRIES = 3


def law_body(name: str) -> str | None:
    """법령 이름(띄어쓰기 무관) → 현행 본문 글자. 연결이 끊기는 경우가 있어 몇 번 다시 시도."""
    url = "https://www.law.go.kr/법령/" + name.replace(" ", "")
    for i in range(TRIES):
        text = fetch(url)
        if text:
            return re.sub(r"\s+", " ", text)
        time.sleep(2 * (i + 1))
    return None


def from_api(name: str, arts: list[str], today: dt.date) -> bool:
    """법제처 공개 API 로 현행 판 조문과 앞으로 바뀌는 내용을 보여 줌. 못 쓰면 False (화면 읽기로)."""
    vers = law_api.versions(name)
    now = law_api.current(vers, today)
    if not now:
        return False
    print(f"[법제처 API] {name} 현행 판: {now['date']:%Y-%m-%d} 시행")
    for art in arts:
        got = law_api.article(name, art, now)
        print(f"\n== {name} {art} ==\n{got['text'] if got else '조문을 찾지 못함'}")
        if not got:
            continue
        for x in exceptions_in(got["text"]):
            print(f"  [예외·단서] {x}")
        for ch in law_api.upcoming_changes(name, art, today, vers):
            print(f"  [시행 예정 개정] {ch['date']:%Y-%m-%d}부터 바뀜 → {ch['text'][:300]}")
    return True


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("사용: python3 -m bot.lawtext <법령명> <제N조> [제M조 ...]  예) python3 -m bot.lawtext 고용보험법 제46조")
        return 2
    try:
        if from_api(argv[1], argv[2:], dt.date.today()):
            return 0
    except (OSError, ValueError, ET.ParseError):  # API 응답 이상 → 화면 읽기로
        pass
    body = law_body(argv[1])
    if not body:
        print(f"{argv[1]}: 열지 못함 (법령 이름을 국가법령정보센터 표기대로, 띄어쓰기 없이)")
        return 1
    for art in argv[2:]:
        text = article_text(body, art)
        print(f"\n== {argv[1]} {art} ==\n{text or '조문을 찾지 못함'}")
        if text:
            for x in exceptions_in(text):
                print(f"  [예외·단서] {x}")
            changed = recent_changes(body, art)
            if changed:
                print(f"  [최근 개정·시행] {', '.join(changed)} — 안내 페이지가 옛 내용일 수 있음")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
