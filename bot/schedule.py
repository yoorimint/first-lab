"""예약 발행: 글은 초안으로 쌓고, '예약 공개 시각'마다 쌓인 초안 중 가장 오래된 것부터 1편씩 공개한다.

설정 페이지 > 발행 방식 '예약 발행' + 예약 공개 시각(한 줄에 하나, 줄 수 = 하루 공개 글 수).
공개 대기열 = 메타 _firstlab_queue='ready' 인 초안. 새 글이 예약 발행 방식으로 저장될 때 붙는다 (publisher.publish).
확인이 필요해 초안으로 남긴 글(제목 숫자 불일치·사실 확인 부족)과 예전 API 봇 초안은 대기열에 넣지 않는다.
"""
from __future__ import annotations

import random
import re
import subprocess
from datetime import date, datetime, timedelta, timezone

from . import freshness, indexnow, naver_hub, seasons
from .settings import wp_cli

QUEUE_META = "_firstlab_queue"
READY = "ready"
LATE_LIMIT = timedelta(hours=3)  # 서버가 멈췄다 켜져도 3시간 넘게 지난 시각은 실행하지 않음 (몰아서 공개 방지)


def plan_times(times: list[str], jitter_minutes: int, today: str) -> list[str]:
    """오늘 실제 시각 = 설정 시각 ± 랜덤(분). 날짜로 시드를 고정해 하루 동안 같은 값, 날짜를 넘지 않게 00:00~23:59."""
    plan = []
    for i, hhmm in enumerate(sorted(times)):
        base = datetime.strptime(f"{today} {hhmm}", "%Y-%m-%d %H:%M")
        offset = random.Random(f"{today}-{i}-{hhmm}").randint(-jitter_minutes, jitter_minutes) if jitter_minutes else 0
        day_start = base.replace(hour=0, minute=0)
        plan.append(min(max(base + timedelta(minutes=offset), day_start), day_start.replace(hour=23, minute=59)))
    # 랜덤으로 두 시각이 같은 분이 되면 '했음' 기록이 하나로 합쳐져 한 편이 공개 안 됨 (11/6 검토: 09:00·10:00 ±100분이면 1년에 2일)
    # → 겹치면 1분씩 뒤로 (자정을 넘으면 앞으로), 줄 수 = 하루 공개 수 그대로
    out: list[datetime] = []
    for t in sorted(plan):
        step = timedelta(minutes=1)
        while t in out:
            if step.days == 0 and t.hour == 23 and t.minute == 59:  # 앞으로 가다 자정 직전
                step = -step   # 자정 직전이면 거꾸로 (한 번만 바꿈 — 무한 반복 없음)
            t += step
        out.append(t)
    return sorted(x.strftime("%H:%M") for x in out)


def due_slot(times: list[str], fired: list[str], now: datetime) -> str | None:
    """오늘의 시각 목록 중 시각이 됐고 아직 처리 안 한 가장 이른 것 (now: 한국 시간, 시간대 정보 없음)."""
    for t in sorted(times):
        hour, minute = map(int, t.split(":"))
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if t not in fired and at <= now < at + LATE_LIMIT:
            return t
    return None


def mark_ready(post_id: int) -> None:
    wp_cli("post", "meta", "update", str(post_id), QUEUE_META, READY)


def ready_drafts() -> list[int]:
    """공개 대기 중인 초안 번호 (오래된 것부터)."""
    out = wp_cli("post", "list", "--post_type=post", "--post_status=draft", f"--meta_key={QUEUE_META}",
                 f"--meta_value={READY}", "--orderby=date", "--order=ASC", "--posts_per_page=-1", "--field=ID")
    return [int(x) for x in out.split()]


def _draft_info(ids: list[int]) -> dict[int, dict]:
    """초안 번호 → {'text': '주소 키워드 + 제목'(계절·황금 점수용), 'date': 쓴 시각(대기 기간용, 못 읽으면 None)} — 한 번의 조회로."""
    import json
    import urllib.parse
    if not ids:
        return {}
    try:
        rows = json.loads(wp_cli("post", "list", "--post_type=post", "--post_status=draft", f"--post__in={','.join(map(str, ids))}",
                                 "--fields=ID,post_name,post_title,post_excerpt,post_date", "--format=json", "--posts_per_page=-1") or "[]")
    except Exception:  # noqa: BLE001 — 못 읽으면 계절 순서 없이 기존 순서
        return {}
    if not isinstance(rows, list):
        return {}
    out = {}
    for r in rows:
        if not isinstance(r, dict) or "ID" not in r:
            continue
        try:
            date = datetime.strptime(str(r.get("post_date", "")), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            date = None
        out[int(r["ID"])] = {"text": urllib.parse.unquote(r.get("post_name", "")) + " " + r.get("post_title", ""), "date": date,
                             "brief": r.get("post_title", "") + " " + (r.get("post_excerpt") or "")}
    return out


def _has_pending_images(post_id) -> bool:
    """한도 소진으로 아직 사진을 못 넣은 글인지 (publisher.PENDING_META)."""
    try:
        return bool(wp_cli("post", "meta", "get", str(post_id), "_firstlab_img_pending").strip())
    except subprocess.CalledProcessError:
        return False


MAX_FRESH_CHECKS = 3  # 한 번에 공개 직전 확인을 하는 초안 수 (봇 1분 실행 시간)
MAX_PHOTO_CHECKS = 20  # 사진이 준비된 글을 찾느라 보는 최대 수 (글마다 메타 조회 1번)
HOLD_META = "_firstlab_hold"  # bot/review_gate.HOLD_META 와 같음 (순환 import 피함)


MAX_WAIT_ANY = timedelta(days=90)  # 주제·황금이 아닌 글도 이만큼 기다리면 중심 주제 무리와 같이
MAX_WAIT = timedelta(days=21)  # 이만큼 기다린 글은 황금 점수와 상관없이 먼저 (계속 밀리지 않게)
# 11/6: 예전엔 '대기열 뒤 21편을 뺀 나머지'로 셈 — 하루 30편씩 쌓이자 하루도 안 된 글까지 '오래된 글'이 돼 황금 점수가 거의 안 쓰임


# 11/11: 기한·시행일이 있는 글(중간예납 11월 30일, 12월 10일 시행 등)이 대기 340편 뒤에 서서 5개월 뒤 공개될 뻔함
# → 제목·요약문에 든 날짜가 오늘부터 URGENT_DAYS 안이면 맨 먼저 (날짜가 빠른 글부터). 날짜가 지나면 원래 순서로.
URGENT_DAYS = 45
# 11/11 운영자 '기한 가까운 것도 그 안에서 황금점수로 줄 세워': 45일 안 무리 안에서는 황금 점수 높은 글부터.
# 다만 SOON_DAYS 안에 날짜가 닥친 글은 날짜 순으로 맨 먼저 (황금 점수에 밀려 날짜가 지나 버리면 공개 직전 검사에서 보류됨).
SOON_DAYS = 7
_DATE = re.compile(r"(?:(20\d\d)년\s*)?(\d{1,2})월\s*(\d{1,2})일")


def due_date(brief: str, today: date) -> date | None:
    """제목·요약문에서 오늘 이후 가장 이른 날짜. 연도가 없으면 앞에 나온 연도, 그것도 없으면 올해(지났으면 다음 해)."""
    found, year = [], None
    for m in _DATE.finditer(brief or ""):
        y = int(m.group(1)) if m.group(1) else None
        year = y or year
        try:
            d = date(y or year or today.year, int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if not (y or year) and d < today:
            d = d.replace(year=today.year + 1)
        if d >= today:
            found.append(d)
    return min(found) if found else None


def urgent_due(brief: str, today: date, days: int = URGENT_DAYS) -> date | None:
    d = due_date(brief, today)
    return d if d and d <= today + timedelta(days=days) else None


def publish_order(ids: list[int], now_kst: datetime) -> list[int]:
    """대기 초안의 공개 순서 (사진 채우기도 이 순서로 — bot/pending_images, 11/6 운영자 '점수 높은 것대로 줄 세우고 그것들 먼저 사진').
    기한·시행일이 45일 안인 글이 맨 먼저(7일 안이면 날짜 순, 나머지는 황금 점수 순, urgent_due) → 시즌 지난 글은 맨 뒤(bot/seasons.py) → 외부 검수 마친 글 → 21일 넘게 기다린 글 → 황금 점수·성수기 높은 글(운영자 지시 11/5,
    bot/naver_hub.py) → 시즌 중인 글 → 오래된 순. 대표 사진이 아직 없는 글은 공개 때 건너뜀(publish_next)."""
    info = _draft_info(ids)
    texts = {i: info.get(i, {}).get("text", "") for i in ids}
    gold = naver_hub.publish_priority({i: texts[i].split(" ")[0] for i in ids}, now_kst.month)
    rank = {i: seasons.rank(texts[i], now_kst.date()) for i in ids}
    from .review_queue import reviewed_ids
    from .keyword_db import norm
    from .select_keyword import tier
    reviewed = reviewed_ids()
    cutoff = now_kst.replace(tzinfo=None) - MAX_WAIT
    old = {i for i in ids if info.get(i, {}).get("date") and info[i]["date"] <= cutoff}
    # 11/7 운영자 '주제도 아니고 황금도 아닌 글은 밀려도 된다': 황금 → 중심 주제 → 좋음 → 나머지 무리 순 (같은 무리 안에서 예전 순서).
    # 아주 오래 기다린 글(MAX_WAIT_ANY)은 중심 주제 무리로 올려 영영 안 나가는 일은 없게.
    labels = naver_hub.publish_labels({i: texts[i].split(" ")[0] for i in ids})
    very_old = now_kst.replace(tzinfo=None) - MAX_WAIT_ANY
    level = {i: tier(labels.get(i, ""), norm(texts[i].split(" ")[0])) for i in ids}
    level = {i: min(t, 1) if info.get(i, {}).get("date") and info[i]["date"] <= very_old else t for i, t in level.items()}
    today = now_kst.date()
    due = {i: urgent_due(info.get(i, {}).get("brief", ""), today) for i in ids}
    soon = {i: due[i] if due[i] and due[i] <= today + timedelta(days=SOON_DAYS) else None for i in ids}
    return sorted(ids, key=lambda i: (due[i] is None, soon[i] is None, soon[i] or today,
                                      -round(gold.get(i, 0.0), 2) if due[i] else 0, due[i] or today,
                                      rank[i] == seasons.OFF, level[i], i not in reviewed,
                                      i not in old, -round(gold.get(i, 0.0), 2), rank[i]))


def draft_signals(ids: list[int]) -> dict[int, dict]:
    """공개 대기 글마다 키워드 검색량·블로그 글 수·황금지수 (bot/naver_hub.draft_signals, /maintenance draft_signals)."""
    info = _draft_info(ids)
    return naver_hub.draft_signals({i: info.get(i, {}).get("text", "").split(" ")[0] for i in ids if info.get(i, {}).get("text")})


UPCOMING = 8


def upcoming(now_kst: datetime, n: int = UPCOMING) -> list[int]:
    """다음에 공개될 초안 n 편 (publish_next 와 같은 순서 — 사진 대기 글은 뺌). 11/10 운영자 '모든 글 90점 이상':
    감시 세션·글쓰기 작업이 공개 전에 이 순서대로 쉬운 말 다듬기를 한다 (/maintenance publish_next)."""
    ids = ready_drafts()
    if not ids:
        return []
    from . import review_queue
    ordered = publish_order(ids, now_kst)
    ordered = review_queue.prefer_prepublished(ordered, review_queue.prepublish_dates(ordered, wp_cli), f"{now_kst:%Y-%m-%d}")
    out = []
    for i in ordered[:MAX_PHOTO_CHECKS + n]:
        if not _has_pending_images(i):
            out.append(i)
            if len(out) >= n:
                break
    return out


def publish_next(now_kst: datetime) -> str | None:
    """가장 오래된 대기 초안 1편 공개 (공개 날짜는 지금). 대기열이 비었으면 None."""
    ids = ready_drafts()
    if not ids:
        return None
    ordered = publish_order(ids, now_kst)
    # 공개 직전 재검수(bot/review_queue)를 마친 글을 먼저 — 없으면 순서 그대로 (공개가 멈추지 않게)
    from . import review_queue
    today = f"{now_kst:%Y-%m-%d}"
    ordered = review_queue.prefer_prepublished(ordered, review_queue.prepublish_dates(ordered, wp_cli), today)
    held = []
    post_id = None
    # 대표 사진이 아직 안 만들어진 글은 이번엔 건너뛰고 다음 순번 (11/9 운영자 '초안에서 해결해서 완벽하게 발행').
    # 사진은 공개 순번대로 먼저 채우므로(pending_images) 보통은 앞 글이 이미 사진을 받아 있음 — 오늘 한도가 찬 뒤 새로 쓴 점수 높은 글만 해당.
    ready = []
    for i in ordered[:MAX_PHOTO_CHECKS]:
        if not _has_pending_images(i):
            ready.append(i)
            if len(ready) >= MAX_FRESH_CHECKS:
                break
    if not ready:
        return f"공개 안 함: 앞쪽 대기 글 {min(len(ordered), MAX_PHOTO_CHECKS)}편 모두 대표 사진 대기 중 (사진 채우기 뒤 다음 공개 때)"
    for candidate in ready:
        try:
            reasons = freshness.check_post(candidate, now_kst.date())
        except Exception:  # noqa: BLE001 — 확인이 실패하면 막지 않음 (바뀌었다는 증거가 있을 때만 보류)
            reasons = []
        if not reasons:  # 본문 공식 주소를 지금 열어 보고 기록 — 연결조차 안 되면 주소가 틀렸을 수 있어 보류 (11/9 운영자 '초안 때 들어가 보고 맞게')
            try:
                from . import official_links
                dead = official_links.run_for(candidate).get("dead") or []
                if dead:
                    reasons = [official_links.dead_reason(dead)]
            except Exception:  # noqa: BLE001 — 확인 자체가 실패하면 막지 않음 (화면 점검이 다시 봄)
                pass
        if not reasons:
            post_id = candidate
            break
        # 쓴 뒤 근거가 바뀜 → 공개하지 않고 보류 (글쓰기 세션이 보류 수정 절차로 다시 확인)
        wp_cli("post", "meta", "update", str(candidate), HOLD_META, ("공개 전 확인: " + "; ".join(reasons))[:300])
        wp_cli("post", "meta", "delete", str(candidate), QUEUE_META)
        held.append(f"{candidate}({reasons[0][:40]})")
    if post_id is None:
        return f"공개 보류 {len(held)}편: {', '.join(held)}" if held else None
    now_utc = now_kst.astimezone(timezone.utc)
    wp_cli("post", "update", str(post_id), "--post_status=publish",
           f"--post_date={now_kst:%Y-%m-%d %H:%M:%S}", f"--post_date_gmt={now_utc:%Y-%m-%d %H:%M:%S}")
    wp_cli("post", "meta", "delete", str(post_id), QUEUE_META)
    title = wp_cli("post", "get", str(post_id), "--field=post_title").strip()
    slug = wp_cli("post", "get", str(post_id), "--field=post_name").strip()
    pinged = indexnow.notify([f"{indexnow.SITE}/{slug}/"]) if slug else "IndexNow: 주소 없음"  # 검색엔진에 바로 알림
    try:  # 새 글이 예전 글의 관련 글이 되게, 공개 글 전체의 관련 글(내용 기준)을 다시 계산 (11/9 운영자, bot/related_posts.py)
        from . import related_posts
        related_posts.run()
    except Exception:  # noqa: BLE001 - 관련 글 계산 실패가 공개를 막지 않게 (하루 한 번 다시 계산)
        pass
    extra = f", 공개 전 확인으로 보류 {', '.join(held)}" if held else ""
    return f"'{title}' 공개 (대기 {len(ids) - 1 - len(held)}편 남음, {pinged}{extra})"
