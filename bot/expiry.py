"""글마다 '다시 확인할 날' (11/7 운영자 '근본적인 해결책 — 글들은 대부분 나중에 업데이트가 필요할 거 아냐').

왜: 공개 뒤 재검증은 법령 개정(law_watch)·근거 페이지 숫자 변화(recheck)·1월/7월·180일처럼 우연한 계기에만 돌았다.
서버가 근거 페이지를 못 열거나(예방접종도우미), 숫자가 아니라 말이 바뀌는 변경(다당 백신 → 단백결합 백신)은 영영 안 잡혔다.
방법: 본문 글자와 마지막 확인일만으로 '다시 확인할 날'을 정한다 (코드만, 저장 없음 — 고치면 다음 계산부터 바로 반영).
  1. 기본 BASE_DAYS 일마다
  2. 서버가 근거를 대부분 못 여는 글은 UNREADABLE_DAYS 일마다 (코드 확인이 안 되니 사람 검수 주기를 줄임)
  3. 본문에 'O년 기준' 값이 있으면 해가 바뀐 뒤 (새해 요율·금액)
  4. 본문에 앞으로의 날짜 + 바뀜 말('부터·시행·예정·전환·마감' 등)이 있으면 그날
  5. '예정' 문장의 날짜가 이미 지났는데 아직 '예정'이면 STALE_PLAN_DAYS 일마다 (바뀐 뒤 글을 고칠 때까지)
기한이 지난 글은 bot/refresh.pick_targets 가 주당 갱신 한도와 상관없이 재검증 대상으로 고르고, 글쓰기 작업이 고치면
마지막 확인일이 오늘이 되어 다음 기한이 다시 계산된다 (평생 보류·무한 반복 없음).
"""
from __future__ import annotations

import datetime as dt
import re

BASE_DAYS = 180
UNREADABLE_DAYS = 90
STALE_PLAN_DAYS = 30
NEW_YEAR_DAY = 5   # 1월 초 고시·공지가 올라온 뒤

CHANGE_WORDS = re.compile(r"부터|시행|예정|전환|바뀌|바꿀|달라|인상|인하|개편|폐지|종료|마감|까지")
PLAN_WORD = re.compile(r"예정|할 계획|바꿀|전환할")
_YMD = re.compile(r"(20\d\d)\s*[년.]\s*(\d{1,2})\s*[월.]\s*(?:(\d{1,2})\s*일?)?")
_YEAR = re.compile(r"(20\d\d)\s*년(?:도)?(?!\s*\d{1,2}\s*월)")
_SENTENCE = re.compile(r"[^.?!。\n]+")


def _date(y: str, m: str, d: str | None) -> dt.date | None:
    try:
        return dt.date(int(y), int(m), int(d or 1))
    except ValueError:
        return None


def dated_sentences(text: str) -> list[tuple[dt.date, str]]:
    """바뀜 말이 든 문장의 날짜들 [(날짜, 문장)] — 'O년 O월 O일', 'O.O.O', 날짜 없이 'O년부터·O년도 … 예정'(1월 1일)."""
    out = []
    for sent in _SENTENCE.findall(text or ""):
        if not CHANGE_WORDS.search(sent):
            continue
        for y, m, d in _YMD.findall(sent):
            day = _date(y, m, d)
            if day:
                out.append((day, sent.strip()))
        for y in _YEAR.findall(sent):
            out.append((dt.date(int(y), 1, 1), sent.strip()))
    return out


def due_date(text: str, checked: dt.date, unreadable: bool = False) -> tuple[dt.date, str]:
    """(다시 확인할 날, 이유) — text: 본문 글자(태그 없이), checked: 마지막으로 확인한 날(공개·검수·갱신 중 가장 늦은 날)."""
    picks = [(checked + dt.timedelta(days=BASE_DAYS), f"{BASE_DAYS}일 주기")]
    if unreadable:
        picks.append((checked + dt.timedelta(days=UNREADABLE_DAYS), f"서버가 근거 페이지를 못 엶 → {UNREADABLE_DAYS}일 주기"))
    if re.search(rf"{checked.year}\s*년", text or ""):
        picks.append((dt.date(checked.year + 1, 1, NEW_YEAR_DAY), f"{checked.year}년 기준 값 — 해 바뀜"))
    dated = dated_sentences(text)
    ahead = {sent for day, sent in dated if day > checked}
    for day, sent in dated:
        if day > checked:
            picks.append((day, f"본문 날짜 {day}: '{sent[:40]}'"))
        elif PLAN_WORD.search(sent) and sent not in ahead:  # 날짜가 다 지났는데 아직 '예정' → 바뀐 뒤 고칠 때까지 자주
            picks.append((checked + dt.timedelta(days=STALE_PLAN_DAYS), f"지난 날짜({day})의 예정 문장: '{sent[:40]}'"))
    return min(picks, key=lambda p: p[0])


def unreadable(source_check: dict | None) -> bool:
    """근거 대조 기록에서 서버가 못 연 근거가 확인된 근거보다 많으면 True."""
    counts = (source_check or {}).get("counts", {}) if isinstance(source_check, dict) else {}
    return int(counts.get("못 엶", 0)) > int(counts.get("확인", 0))


def last_checked(post: dict) -> dt.date:
    """공개일·검수 통과일·갱신일 중 가장 늦은 날 (못 읽으면 공개일)."""
    days = []
    for key in ("date", "verified", "refreshed"):
        try:
            days.append(dt.date.fromisoformat(str(post.get(key) or "")[:10]))
        except ValueError:
            pass
    return max(days) if days else dt.date.today()
