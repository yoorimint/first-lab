"""한도 소진으로 못 만든 대표 사진 채우기 (봇 1분 타이머에서 5분마다, 운영자 손 없이).

Cloudflare 무료 한도(하루 10,000 뉴런 ≈ 사진 50장)가 다 되면 글은 제목 카드를 대표 이미지로 저장되고,
장면 설명이 글 메타(photo.PENDING_META)에 남는다. 한도가 초기화되면(오전 9시) 공개 대기열 글부터 채운다.
새로 넣은 사진은 사진 검사(image_check) 대상이 되도록 검사 완료 표시를 지운다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import images, photo, schedule
from .image_check import CHECKED_META
from .publisher import existing_post_id
from .schedule import QUEUE_META, READY
from .settings import wp_cli

QUEUE_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "queue"
KST = ZoneInfo("Asia/Seoul")


def pending_posts(now_kst: datetime | None = None) -> list[str]:
    """사진을 기다리는 글 번호: 이미 공개된 글 먼저(방문자에게 제목 카드가 보이는 중 — 11/6 #583),
    그다음 공개 대기열 글을 **공개 순서대로**(황금 점수 높은 글이 먼저 공개되니 그 글부터 — 11/6 운영자), 나머지는 오래된 순."""
    def ids(status: str) -> list[str]:
        return wp_cli("post", "list", "--post_type=post", f"--post_status={status}",
                      f"--meta_key={photo.PENDING_META}", "--orderby=date", "--order=ASC", "--field=ID").split()
    drafts = ids("draft")
    ready = [i for i in drafts if photo.meta(i, QUEUE_META) == READY]
    order = {str(pid): n for n, pid in enumerate(schedule.publish_order([int(i) for i in ready], now_kst or datetime.now(KST)))} if ready else {}
    return ids("publish") + sorted(ready, key=lambda i: order.get(i, len(order))) + [i for i in drafts if i not in ready]


def fill_post(post_id: str, cloudflare: tuple[str, str]) -> str:
    """글 하나의 대표 사진 만들기. 한도가 또 차면 그대로 두고 다음에."""
    scenes = json.loads(photo.meta(post_id, photo.PENDING_META) or "[]")
    scene = photo.first_scene([s.get("prompt", "") for s in scenes], [s.get("alt", "") for s in scenes])
    current = photo.meta(post_id, photo.PHOTO_META)
    target = scenes[0].get("replace", "") if scenes else ""  # 사진 검사에서 바꾸기로 한 사진 (image_check)
    if scene and (not current or (target and target == current)):
        photo.make(post_id, scene, cloudflare)  # QuotaExceeded 면 메타를 남긴 채 위로 전달
        wp_cli("post", "meta", "delete", post_id, photo.PENDING_META)
        photo._delete_meta(post_id, CHECKED_META)  # 새 사진도 사진 검사 받게
        return f"글 {post_id} 대표 사진 {'교체' if current else '추가'}"
    wp_cli("post", "meta", "delete", post_id, photo.PENDING_META)
    return f"글 {post_id} 대기 정리 (이미 사진 있음·쓸 장면 없음)"


def fill(cloudflare: tuple[str, str], max_posts: int = 2) -> list[str]:
    if not all(cloudflare) or images.quota_blocked():
        return []
    out = []
    for post_id in pending_posts()[:max_posts]:
        try:
            out.append(fill_post(post_id, cloudflare))
        except images.QuotaExceeded:
            out.append("사진 한도 소진 — 다음 시도는 서버가 정한 시각에 (images.quota_blocked)")
            break
        except Exception as e:  # noqa: BLE001 - 한 글의 실패(안전 필터·5xx)가 다른 글을 영영 막지 않게 (11/5 검토)
            tries = _bump_fail(post_id)
            if tries >= MAX_FAILS:
                wp_cli("post", "meta", "delete", post_id, photo.PENDING_META)  # 제목 카드로 둠
                out.append(f"글 {post_id} 사진 {tries}번 실패 → 제목 카드로 둠 ({str(e)[:60]})")
            else:
                out.append(f"글 {post_id} 사진 실패 {tries}/{MAX_FAILS} ({str(e)[:60]})")
    return out


FAIL_META = "_firstlab_img_fails"
MAX_FAILS = 3


def _bump_fail(post_id: str) -> int:
    try:
        n = int(wp_cli("post", "meta", "get", str(post_id), FAIL_META).strip() or 0) + 1
    except Exception:  # noqa: BLE001
        n = 1
    wp_cli("post", "meta", "update", str(post_id), FAIL_META, str(n))
    return n


def backfill_from_queue() -> list[str]:
    """대표 사진 없이(제목 카드로) 저장된 직접 작성 글: 큐 파일의 장면 설명으로 대기 표시."""
    out = []
    for path in sorted(QUEUE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("type") or not data.get("images") or not data.get("keyword"):
            continue
        post_id = existing_post_id(str(data["keyword"]))
        if not post_id or photo.meta(post_id, photo.PHOTO_META) or photo.meta(post_id, photo.PENDING_META):
            continue
        scene = photo.first_scene([i.get("prompt", "") for i in data["images"]], [i.get("alt", "") for i in data["images"]])
        if scene:
            wp_cli("post", "meta", "update", post_id, photo.PENDING_META, json.dumps([scene], ensure_ascii=False))
            out.append(post_id)
    return out


if __name__ == "__main__":
    # 배포 때: 대표 사진 없이 저장된 글 표시 (한도 초기화 후 봇이 채움)
    done = backfill_from_queue()
    print(f"[사진 채우기] 대표 사진 기다리는 글 표시: {', '.join(done) if done else '없음'}", flush=True)
