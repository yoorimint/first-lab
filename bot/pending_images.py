"""한도 소진으로 못 만든 본문 사진 채우기 (봇 1분 타이머에서 실행, 운영자 손 없이).

Cloudflare 무료 한도(하루 10,000 뉴런 ≈ 사진 50장)가 다 되면 글은 사진 없이 저장되고,
못 만든 사진의 장면 설명이 글 메타(publisher.PENDING_META)에 남는다.
한도가 초기화되면(오전 9시) 공개 순서가 빠른 글(공개 대기열)부터 사진을 만들어 넣는다.
새로 넣은 사진은 사진 검사(image_check) 대상이 되도록 검사 완료 표시를 지운다.
"""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from . import images
from .image_check import CHECKED_META
from .publisher import PENDING_META, _insert_images
from .schedule import QUEUE_META, READY
from .settings import wp_cli

MAX_IMAGES = 3
QUEUE_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "queue"


def _meta(post_id: str, key: str) -> str:
    try:
        return wp_cli("post", "meta", "get", post_id, key).strip()
    except subprocess.CalledProcessError:
        return ""


def pending_posts() -> list[str]:
    """사진이 남은 글 번호 (공개 대기열 글 먼저, 그 안에서는 오래된 순)."""
    ids = wp_cli("post", "list", "--post_type=post", "--post_status=draft,publish",
                 f"--meta_key={PENDING_META}", "--orderby=date", "--order=ASC", "--field=ID").split()
    return sorted(ids, key=lambda i: _meta(i, QUEUE_META) != READY)


def fill_post(post_id: str, account_id: str, token: str) -> str:
    """글 하나의 남은 사진 만들기. 한도가 또 차면 만든 것까지만 넣고 나머지는 남겨 둔다."""
    pending = json.loads(_meta(post_id, PENDING_META) or "[]")
    content = wp_cli("post", "get", post_id, "--field=post_content")
    room = MAX_IMAGES - content.count("/ai-")
    todo, rest = pending[:max(room, 0)], []
    blocks = []
    for i, item in enumerate(todo):
        try:
            path = images.cloudflare_image(account_id, token, item["prompt"])
        except images.QuotaExceeded:
            rest = todo[i:]
            break
        _, url = images.import_media(path, item.get("alt", ""), item.get("alt", ""))
        blocks.append(images.image_block(url, item.get("alt", "")))
    if blocks:
        wp_cli("post", "update", post_id, f"--post_content={_insert_images(content, blocks)}")
        try:
            wp_cli("post", "meta", "delete", post_id, CHECKED_META)  # 새 사진도 사진 검사 받게
        except subprocess.CalledProcessError:
            pass
    if rest:
        wp_cli("post", "meta", "update", post_id, PENDING_META, json.dumps(rest, ensure_ascii=False))
    else:
        wp_cli("post", "meta", "delete", post_id, PENDING_META)
    return f"글 {post_id} 사진 {len(blocks)}장 추가" + (f" ({len(rest)}장은 한도 초기화 후)" if rest else "")


def fill(account_id: str, token: str, max_posts: int = 1) -> list[str]:
    """1분마다: 한도가 막혀 있지 않으면 글 max_posts 개 채움."""
    if not (account_id and token) or images.quota_blocked():
        return []
    out = []
    for post_id in pending_posts()[:max_posts]:
        out.append(fill_post(post_id, account_id, token))
        if images.quota_blocked():
            break
    return out


_FIND_PHP = ('$p = get_page_by_path( sanitize_title( base64_decode( "%s" ) ), OBJECT, "post" ); '
             'echo $p ? $p->ID : "";')


def backfill_from_queue() -> list[str]:
    """이 기능 전에 사진 없이 저장된 글: 큐 파일의 장면 설명으로 남은 사진 표시 (사진이 이미 있으면 건드리지 않음)."""
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
        if not post_id or _meta(post_id, PENDING_META):
            continue
        if "/ai-" in wp_cli("post", "get", post_id, "--field=post_content"):
            continue
        items = [{"prompt": i.get("prompt", ""), "alt": i.get("alt", "")} for i in data["images"][:MAX_IMAGES]
                 if i.get("prompt") and not images.text_prone(i["prompt"])]
        if items:
            wp_cli("post", "meta", "update", post_id, PENDING_META, json.dumps(items, ensure_ascii=False))
            out.append(post_id)
    return out


if __name__ == "__main__":
    # 배포 때: 사진 없이 저장된 글 표시
    done = backfill_from_queue()
    print(f"[사진 채우기] 사진 없는 글 표시: {', '.join(done) if done else '없음'}", flush=True)
