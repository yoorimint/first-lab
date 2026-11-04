"""한도 소진으로 못 만든 대표 사진 채우기 (봇 1분 타이머에서 5분마다, 운영자 손 없이).

Cloudflare 무료 한도(하루 10,000 뉴런 ≈ 사진 50장)가 다 되면 글은 제목 카드를 대표 이미지로 저장되고,
장면 설명이 글 메타(photo.PENDING_META)에 남는다. 한도가 초기화되면(오전 9시) 공개 대기열 글부터 채운다.
새로 넣은 사진은 사진 검사(image_check) 대상이 되도록 검사 완료 표시를 지운다.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from . import images, photo
from .image_check import CHECKED_META
from .schedule import QUEUE_META, READY
from .settings import wp_cli

QUEUE_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "queue"


def pending_posts() -> list[str]:
    """사진을 기다리는 글 번호 (공개 대기열 글 먼저, 그 안에서는 오래된 순)."""
    ids = wp_cli("post", "list", "--post_type=post", "--post_status=draft,publish",
                 f"--meta_key={photo.PENDING_META}", "--orderby=date", "--order=ASC", "--field=ID").split()
    return sorted(ids, key=lambda i: photo.meta(i, QUEUE_META) != READY)


def fill_post(post_id: str, cloudflare: tuple[str, str]) -> str:
    """글 하나의 대표 사진 만들기. 한도가 또 차면 그대로 두고 다음에."""
    scenes = json.loads(photo.meta(post_id, photo.PENDING_META) or "[]")
    scene = photo.first_scene([s.get("prompt", "") for s in scenes], [s.get("alt", "") for s in scenes])
    if scene and not photo.meta(post_id, photo.PHOTO_META):
        photo.make(post_id, scene, cloudflare)  # QuotaExceeded 면 메타를 남긴 채 위로 전달
        wp_cli("post", "meta", "delete", post_id, photo.PENDING_META)
        photo._delete_meta(post_id, CHECKED_META)  # 새 사진도 사진 검사 받게
        return f"글 {post_id} 대표 사진 추가"
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
            break
    return out


_FIND_PHP = ('$p = get_page_by_path( sanitize_title( base64_decode( "%s" ) ), OBJECT, "post" ); '
             'echo $p ? $p->ID : "";')


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
        key = base64.b64encode(str(data["keyword"]).strip().encode()).decode()
        post_id = wp_cli("eval", _FIND_PHP % key).strip()
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
