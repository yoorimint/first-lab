"""공개 글이 네이버 검색에 나오는지 하루 한 번 확인 — /maintenance 'naver', 사이트 성과 '글별 색인 상태'의 네이버 칸.

11/16 운영자 '넣어라': 구글 색인을 기다리는 동안 네이버 방문자도 애드센스 '실제 사용자 관심'에 들어감.
제목 그대로 네이버 웹 검색을 열어 결과에 first-lab.kr 주소가 있으면 '나옴' (검색 API 가 아니라 검색 화면 — 11/11 운영자 지시,
automation/naver_top.py 와 같은 방식·요청 사이 쉼). 하루 CHECK_PER_DAY 편까지, 아직 안 나온 글·오래 안 본 글부터.
"""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

SEARCH = "https://search.naver.com/search.naver?{q}"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"  # automation/naver_top.UA 와 같게
OWN = "first-lab.kr"
CHECK_PER_DAY = 60
PAUSE = 1.5   # 네이버에 부담 주지 않게 요청 사이 쉼


def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def found(title: str, get=fetch) -> bool:
    """제목 그대로 네이버 웹 검색에 우리 주소가 나오는지."""
    page = get(SEARCH.format(q=urllib.parse.urlencode({"where": "web", "query": title})))
    return OWN in page


def due(posts: list[dict], known: dict, today: str, limit: int = CHECK_PER_DAY) -> list[dict]:
    """오늘 볼 글: 안 나온 글(오래 안 본 순) → 나온 글(오래 안 본 순). 오늘 이미 본 글은 뺌."""
    todo = [p for p in posts if (known.get(str(p["post_id"])) or {}).get("checked") != today]
    todo.sort(key=lambda p: (bool((known.get(str(p["post_id"])) or {}).get("found")),
                             (known.get(str(p["post_id"])) or {}).get("checked") or ""))
    return todo[:limit]


def run(posts: list[dict], known: dict, today: str, get=fetch, pause: float = PAUSE) -> tuple[dict, list[str]]:
    """{글 번호: {found, checked, first_seen}} 갱신본과 실패 목록. 한 글 실패는 건너뛰고 다음 날 다시."""
    known = {k: dict(v) for k, v in (known or {}).items()}
    errors: list[str] = []
    for i, p in enumerate(due(posts, known, today)):
        if i and pause:
            time.sleep(pause)
        try:
            ok = found(p["title"], get)
        except Exception as e:  # noqa: BLE001 - 네이버가 잠깐 안 열려도 다른 글은 계속
            errors.append(f"{p['post_id']}: {type(e).__name__}")
            if len(errors) >= 3:
                break
            continue
        row = known.setdefault(str(p["post_id"]), {})
        if ok and not row.get("first_seen"):
            row["first_seen"] = today
        row.update(found=ok, checked=today)
    return known, errors


def published_posts(wp=None) -> list[dict]:
    import json

    from .settings import wp_cli
    out = (wp or wp_cli)("post", "list", "--post_type=post", "--post_status=publish", "--fields=ID,post_title", "--format=json")
    return [{"post_id": int(p["ID"]), "title": re.sub(r"\s+", " ", str(p["post_title"])).strip()} for p in json.loads(out or "[]")]


def record(state: dict, today: str | None = None, posts: list[dict] | None = None, get=fetch) -> str:
    """하루 한 번 (새벽 수집 bot/discover.py). state['naver'] = {date, found, published, posts: {글 번호: {...}}}."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    old = state.get("naver") or {}
    if old.get("date") == today:
        return ""
    posts = posts if posts is not None else published_posts()
    known, errors = run(posts, old.get("posts") or {}, today, get=get)
    live = {str(p["post_id"]) for p in posts}
    known = {k: v for k, v in known.items() if k in live}   # 내린 글은 뺌
    n = sum(1 for v in known.values() if v.get("found"))
    state["naver"] = {"date": today, "found": n, "published": len(posts), "posts": known}
    return f"네이버 노출({today}): 공개 {len(posts)}편 중 {n}편 검색에 나옴" + (f" (확인 실패 {len(errors)}편)" if errors else "")
