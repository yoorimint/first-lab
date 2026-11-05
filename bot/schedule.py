"""예약 발행: 글은 초안으로 쌓고, '예약 공개 시각'마다 쌓인 초안 중 가장 오래된 것부터 1편씩 공개한다.

설정 페이지 > 발행 방식 '예약 발행' + 예약 공개 시각(한 줄에 하나, 줄 수 = 하루 공개 글 수).
공개 대기열 = 메타 _firstlab_queue='ready' 인 초안. 새 글이 예약 발행 방식으로 저장될 때 붙는다 (publisher.publish).
확인이 필요해 초안으로 남긴 글(제목 숫자 불일치·사실 확인 부족)과 예전 API 봇 초안은 대기열에 넣지 않는다.
"""
from __future__ import annotations

import random
import subprocess
from datetime import datetime, timedelta, timezone

from . import freshness, indexnow, seasons
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
        plan.append(min(max(base + timedelta(minutes=offset), day_start), day_start.replace(hour=23, minute=59)).strftime("%H:%M"))
    return sorted(plan)


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


def _draft_texts(ids: list[int]) -> dict[int, str]:
    """초안 번호 → '주소 키워드 + 제목' (한 번의 조회로, 계절 판단용)."""
    import json
    import urllib.parse
    if not ids:
        return {}
    try:
        rows = json.loads(wp_cli("post", "list", "--post_type=post", "--post_status=draft", f"--post__in={','.join(map(str, ids))}",
                                 "--fields=ID,post_name,post_title", "--format=json", "--posts_per_page=-1") or "[]")
    except Exception:  # noqa: BLE001 — 못 읽으면 계절 순서 없이 기존 순서
        return {}
    if not isinstance(rows, list):
        return {}
    return {int(r["ID"]): urllib.parse.unquote(r.get("post_name", "")) + " " + r.get("post_title", "")
            for r in rows if isinstance(r, dict) and "ID" in r}


def _has_pending_images(post_id) -> bool:
    """한도 소진으로 아직 사진을 못 넣은 글인지 (publisher.PENDING_META)."""
    try:
        return bool(wp_cli("post", "meta", "get", str(post_id), "_firstlab_img_pending").strip())
    except subprocess.CalledProcessError:
        return False


MAX_FRESH_CHECKS = 3  # 한 번에 공개 직전 확인을 하는 초안 수 (봇 1분 실행 시간)
HOLD_META = "_firstlab_hold"  # bot/review_gate.HOLD_META 와 같음 (순환 import 피함)


def publish_next(now_kst: datetime) -> str | None:
    """가장 오래된 대기 초안 1편 공개 (공개 날짜는 지금). 대기열이 비었으면 None."""
    ids = ready_drafts()
    if not ids:
        return None
    texts = _draft_texts(ids)
    # 시즌 중인 글 먼저, 시즌 지난 글은 뒤로 (bot/seasons.py) → 그 안에서 사진이 다 들어간 글 먼저 → 오래된 순
    ordered = sorted(ids, key=lambda i: (seasons.rank(texts.get(i, ""), now_kst.date()), _has_pending_images(i)))
    held = []
    post_id = None
    for candidate in ordered[:MAX_FRESH_CHECKS]:
        try:
            reasons = freshness.check_post(candidate, now_kst.date())
        except Exception:  # noqa: BLE001 — 확인이 실패하면 막지 않음 (바뀌었다는 증거가 있을 때만 보류)
            reasons = []
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
    extra = f", 공개 전 확인으로 보류 {', '.join(held)}" if held else ""
    return f"'{title}' 공개 (대기 {len(ids) - 1 - len(held)}편 남음, {pinged}{extra})"
