"""법령 변경 감시 (하루 1번, 키워드 수집 끝에 실행 — Claude 사용량 없음, 11/5 운영자 승인).

우리 글들이 근거로 쓴 법령(검수 기록의 law.go.kr 주소) 전체를 매일 열어 부칙의 시행일을 읽는다.
  - 앞으로 시행될 개정(오늘 ~ LOOKAHEAD_DAYS 안) → state["law_changes"] 에 저장
    → 읽기 API(/next) 의 law_changes 로 글쓰기 세션에 '바뀌는 제도' 글감으로 전달 (시행 몇 주 전에 선점)
  - 막 시행된 개정(최근 JUST_DAYS 안) → 그 법을 근거로 한 공개 글에 표시(LAW_META)
    → 글 갱신(bot/refresh.py)이 가장 먼저 '수치 재검증' 대상으로 고른다
공개 전 초안은 공개 직전 확인(bot/freshness.py)이 따로 막는다.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import json
import re
import urllib.parse
from pathlib import Path

from . import source_check
from .publisher import existing_post_id
from .review_gate import _ok
from .settings import wp_cli
from .state import add_log, load_state, save_state

QUEUE_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "queue"
LOOKAHEAD_DAYS = 120   # 이 안에 시행되는 개정을 글감으로
JUST_DAYS = 30         # 최근 이 안에 시행된 개정은 기존 글 재검증
MAX_LAWS = 150         # 하루에 여는 법령 수
WORKERS = 6            # 동시에 여는 수 (law.go.kr 이 끊지 않을 정도)
LAW_META = "_firstlab_law_changed"  # '시행일 법령명' — 갱신 대상 표시
_EFFECTIVE = re.compile(r"(.{0,160}?)(?:개정규정은|이 법은|이 영은|이 규칙은|이 고시는)\s*(20\d\d)년\s*(\d{1,2})월\s*(\d{1,2})일부터\s*시행")


def law_name(base: str) -> str:
    plain = urllib.parse.unquote(base)
    return plain.rsplit("/", 1)[-1] if "/법령/" in plain else plain[-40:]


def watched_laws() -> dict[str, set[str]]:
    """근거 법령 주소 → 그 법을 근거로 쓴 글 키워드들 (큐 파일의 검수 기록에서)."""
    laws: dict[str, set[str]] = {}
    for path in QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        keyword = str(data.get("keyword", "")).strip()
        for item in data.get("review") or []:
            if not _ok(item):
                continue
            for url in item.get("urls", []):
                key = law_key(str(url))
                if key:
                    laws.setdefault(key, set()).add(keyword)
    return laws


def law_key(url: str) -> str | None:
    """같은 법령을 하나로: '/법령/이름(/제N조)' → '/법령/이름', 조문 본문 주소는 lsiSeq 로.
    별표 PDF(flDownload)·조문 연결(lsLawLinkInfo) 등 본문 시행일을 못 읽는 주소는 뺀다."""
    if "law.go.kr" not in url:
        return None
    plain = urllib.parse.unquote(url)
    if "/법령/" in plain:
        return source_check.law_page(url)
    m = re.search(r"lsiSeq=(\d+)", url)
    if m and "lsInfo" in url:
        efyd = re.search(r"efYd=(\d+)", url)
        return f"https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq={m.group(1)}" + (f"&efYd={efyd.group(1)}" if efyd else "")
    return None


def title_of(text: str) -> str:
    """법령 본문 머리의 이름 ('… 고용보험법 [시행 2026. 9. 18.]' → '고용보험법')."""
    m = re.search(r"([가-힣·ㆍ ]{2,40}?)\s*\[시행", text[:600])
    words = m.group(1).split() if m else []
    if not words:
        return ""
    if words[-1] in ("시행령", "시행규칙") and len(words) >= 2:  # '법인세법 시행령'
        return f"{words[-2]} {words[-1]}"
    return words[-1]


def effective_dates(text: str) -> list[tuple[dt.date, list[str]]]:
    """(시행일, 그 시행일에 바뀌는 조문들 — 모르면 빈 목록)."""
    out = []
    for ctx, y, m, d in _EFFECTIVE.findall(text):
        try:
            when = dt.date(int(y), int(m), int(d))
        except ValueError:
            continue
        ctx = re.sub(r"제1조\s*\(\s*시행일\s*\)", "", ctx)  # 부칙 '제1조(시행일)' 은 바뀌는 조문이 아님
        out.append((when, sorted(set(re.findall(r"제\d+조(?:의\d+)?", ctx)))))
    return out


def scan(laws: dict[str, set[str]], today: dt.date, fetcher=source_check.fetch) -> tuple[list[dict], list[dict]]:
    """(앞으로 시행될 개정, 막 시행된 개정)."""
    upcoming, just, seen = [], [], set()
    bases = sorted(laws)[:MAX_LAWS]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        texts = dict(zip(bases, pool.map(fetcher, bases)))
    for base in bases:
        text = texts.get(base)
        if not text:
            continue
        name = title_of(text) or law_name(base)
        for when, articles in effective_dates(text):
            key = (name, when, tuple(articles))
            if key in seen:  # 같은 법을 다른 주소로 적은 경우 한 번만
                for item in upcoming + just:
                    if (item["law"], item["date"], tuple(item["articles"])) == (name, f"{when:%Y-%m-%d}", tuple(articles)):
                        item["posts"] = sorted(set(item["posts"]) | laws[base])[:10]
                continue
            seen.add(key)
            item = {"law": name, "url": base, "date": f"{when:%Y-%m-%d}", "articles": articles,
                    "posts": sorted(p for p in laws[base] if p)[:10], "key": f"{name}|{when:%Y-%m-%d}"}
            if today < when <= today + dt.timedelta(days=LOOKAHEAD_DAYS):
                upcoming.append(item)
            elif today - dt.timedelta(days=JUST_DAYS) <= when <= today:
                just.append(item)
    upcoming.sort(key=lambda x: x["date"])
    return upcoming, just


def mark_posts(just: list[dict]) -> list[str]:
    """막 시행된 개정의 근거 글(공개된 것)에 재검증 표시."""
    marked = []
    for item in just:
        for keyword in item["posts"]:
            post_id = existing_post_id(keyword) if keyword else ""
            if not post_id or wp_cli("post", "get", post_id, "--field=post_status").strip() != "publish":
                continue
            value = f"{item['date']} {item['law']}"
            if wp_cli("post", "meta", "get", post_id, LAW_META).strip() != value:
                wp_cli("post", "meta", "update", post_id, LAW_META, value)
                marked.append(post_id)
    return marked


def run(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    laws = watched_laws()
    upcoming, just = scan(laws, today)
    marked = mark_posts(just)
    state = load_state()  # 저장 때 다른 실행 기록과 합쳐짐 (bot/state.save_state)
    state["law_changes"] = {"checked": f"{today:%Y-%m-%d}", "upcoming": upcoming[:30]}
    msg = f"법령 감시: 근거 법령 {len(laws)}개 확인, 앞으로 시행 {len(upcoming)}건, 막 시행 {len(just)}건(재검증 표시 {len(marked)}편)"
    add_log(state, True, msg)
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
