"""글마다 '다시 확인할 날' (11/7 운영자 '근본적인 해결책 — 글들은 대부분 나중에 업데이트가 필요할 거 아냐').

왜: 공개 뒤 재검증은 법령 개정(law_watch)·근거 페이지 숫자 변화(recheck)·1월/7월·180일처럼 우연한 계기에만 돌았다.
서버가 근거 페이지를 못 열거나(예방접종도우미), 숫자가 아니라 말이 바뀌는 변경(다당 백신 → 단백결합 백신)은 영영 안 잡혔다.
방법: 본문 글자와 마지막 확인일만으로 '다시 확인할 날'을 정한다 (코드만, 저장 없음 — 고치면 다음 계산부터 바로 반영).
  1. 기본 BASE_DAYS 일마다
  2. 서버가 근거를 대부분 못 여는 글은 UNREADABLE_DAYS 일마다 (코드 확인이 안 되니 사람 검수 주기를 줄임)
  3. 본문에 'O년 기준' 값이 있으면 해가 바뀐 뒤 (새해 요율·금액)
  4. 본문에 앞으로의 날짜 + 바뀜 말('부터·시행·예정·전환·마감' 등)이 있으면 그날
  5. '예정' 문장의 날짜가 이미 지났는데 아직 '예정'이면 STALE_PLAN_DAYS 일마다 (바뀐 뒤 글을 고칠 때까지)
  연도 없는 'O월 O일'(11/7 #1423 '11월 19일 접수')은 확인한 날 기준 가장 가까운 해로 읽는다 (_infer).
공개 전 대기 초안: passed_rounds — 쓸 때 앞날이던 접수·신청·마감 날짜가 공개 전에 지났으면 보류 (bot/freshness.check_post).
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
_MD = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_RANGE_END = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*\d{1,2}\s*일?\s*(?:\([^)]{1,3}\)\s*)?[~∼–-]\s*(\d{1,2})\s*일")  # '11월 2일~30일' 의 끝날
_YEAR = re.compile(r"(20\d\d)\s*년(?:도)?(?!\s*\d{1,2}\s*월)")
_SENTENCE = re.compile(r"[^.?!。\n]+")


def _date(y: str, m: str, d: str | None) -> dt.date | None:
    try:
        return dt.date(int(y), int(m), int(d or 1))
    except ValueError:
        return None


LOOKBACK_DAYS = 45  # 연도 없는 날짜가 기준일보다 이만큼 넘게 앞이면 다음 해로 읽음


def _infer(m: str, d: str, ref: dt.date) -> dt.date | None:
    """연도 없는 'O월 O일' → ref 기준 가장 가까운 해 (11월에 쓴 '1월 9일'은 다음 해)."""
    day = _date(str(ref.year), m, d)
    if day and day < ref - dt.timedelta(days=LOOKBACK_DAYS):
        day = _date(str(ref.year + 1), m, d)
    return day


def sentence_dates(sent: str, ref: dt.date | None = None) -> list[dt.date]:
    """문장 속 날짜 — 'O년 O월 O일'·'O.O.O' 먼저, 그 밖의 연도 없는 'O월 O일'은 ref 가 있으면 ref 기준 해로."""
    days = [day for y, m, d in _YMD.findall(sent) for day in [_date(y, m, d)] if day]
    if ref:
        rest = _YMD.sub(" ", sent)
        days += [day for m, d in _MD.findall(rest) + _RANGE_END.findall(rest) for day in [_infer(m, d, ref)] if day]
    return days


def dated_sentences(text: str, ref: dt.date | None = None) -> list[tuple[dt.date, str]]:
    """바뀜 말이 든 문장의 날짜들 [(날짜, 문장)] — 'O년 O월 O일', 'O.O.O', 날짜 없이 'O년부터·O년도 … 예정'(1월 1일),
    ref 가 있으면 연도 없는 'O월 O일'도."""
    out = []
    for sent in _SENTENCE.findall(text or ""):
        if not CHANGE_WORDS.search(sent):
            continue
        out += [(day, sent.strip()) for day in sentence_dates(sent, ref)]
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
    dated = dated_sentences(text, checked)
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


# 공개 전 대기 초안의 지난 회차 (11/7 운영자 '시간 제한 있는 글은 나중에 올리면 기한이 지나지 않아?')
ROUND_WORDS = re.compile(r"접수|신청|원서|모집|마감|추첨|예매|선착순|응시")
RECURRING = re.compile(r"매년|매달|매월|해마다|매 ?분기")  # 해마다 같은 날의 규칙 — 지난 회차가 아님
PAST_WORDS = re.compile(r"됐|었|였|했|지났|끝났|끝난|지난|이후")  # 이미 지난 일로 쓴 문장·'O일 이후 신청분부터'(규칙 변경)
_BLOCK = re.compile(r"</?(?:p|li|tr|td|th|h[1-6]|div|br|figcaption|caption)\b[^>]*>", re.I)


def blocks(html_: str) -> list[str]:
    """본문 HTML → 문단·목록·표 행 단위 글자 (표 전체가 한 문장으로 붙지 않게)."""
    import html as _html
    parts = (_html.unescape(re.sub(r"<[^>]+>", " ", b)) for b in _BLOCK.split(html_ or ""))
    return [re.sub(r"\s+", " ", b).strip() for b in parts if b.strip()]


def passed_rounds(texts: list[str], written: dt.date, today: dt.date) -> list[str]:
    """쓸 때(written)는 앞날이던 접수·신청·마감 날짜가 오늘 전에 지난 문장 → 공개하면 지난 일정을 앞으로 할 일처럼 보여 줌."""
    out = []
    for text in texts:
        for sent in _SENTENCE.findall(text or ""):
            if not ROUND_WORDS.search(sent) or PAST_WORDS.search(sent) or RECURRING.search(sent):
                continue
            days = sentence_dates(sent, written)
            last = max(days) if days else None  # 기간이면 끝날까지는 아직 유효
            if last and written <= last < today:
                out.append(f"쓴 뒤 지난 일정 {last}: '{sent.strip()[:50]}' — 다음 회차로 고치거나 지난 일로 쓰기")
    return list(dict.fromkeys(out))[:3]


# 되풀이되는 일정 글의 제목·요약에 이번 회차 날짜 (11/7 운영자 '시험일정이 아니라 계속 유효한 걸로 적어야 되는 거 아냐?')
_ROUND_HEAD = re.compile(r"(?<!\d)\d{1,2}\s*월\s*(?:\d{1,2}\s*일[^,.·:|]{0,12}?)?(?:접수|신청|원서|모집|추첨|예매|선착순|시험|회차)")
# 해마다 같은 날(매년·정기)은 뺌 — 그건 'O년 기준' 새해 다시 확인(due_date)이 맡음
REPEATS = re.compile(r"매달|매월|월별|회차|분기마다|(?<!\d)\d차(?![가-힣])|제\s?\d+회")


def round_headline(title: str, excerpt: str, body_text: str) -> list[str]:
    """되풀이되는 일정(본문에 매달·회차·정기 등)인데 제목·요약이 이번 회차 날짜에 묶인 곳 → 회차가 지나면 글 전체가 지난 글이 됨."""
    if not REPEATS.search(body_text or ""):
        return []
    out = []
    for where, text in (("제목", title or ""), ("요약", excerpt or "")):
        m = _ROUND_HEAD.search(text)
        if m and "이후" not in m.group(0):
            out.append(f"{where}에 이번 회차 날짜 '{m.group(0).strip()}' — 되풀이되는 일정이면 늘 맞는 규칙(예: '매달 시행, 전달 중순 접수')으로 쓰고"
                       " 이번 회차 날짜는 본문 'O월 O일 기준 다음 회차' 칸에")
    return out
