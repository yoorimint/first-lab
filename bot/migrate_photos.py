"""기존 글 정리 (배포 때 실행, 여러 번 실행해도 안전): 본문 AI 사진 → 대표 사진 1장으로.

본문의 첫 AI 사진을 대표 이미지로 옮기고(이미 대표 사진이 있으면 그대로), 본문의 AI 사진 블록은 모두 뺀다.
대표 사진으로 쓰지 않는 사진은 미디어에서 지운다. 본문에 AI 사진이 없는 글은 건드리지 않는다.
"""
from __future__ import annotations

import re

from . import images, photo
from .settings import wp_cli

_AI_SRC = r'https?://[^"\s]*/(ai-[0-9a-f]{10})(?:-\d+x\d+)?\.jpg'
_BLOCK = re.compile(r"<!-- wp:image\b(?:(?!<!-- /wp:image -->).)*?" + _AI_SRC + r".*?<!-- /wp:image -->\s*", re.S)


def body_photos(content: str) -> list[str]:
    """본문 AI 사진 이름 (순서대로)."""
    return [m.group(1) for m in _BLOCK.finditer(content)]


def strip_photos(content: str) -> str:
    return _BLOCK.sub("", content)


def _attachment_id(name: str) -> str:
    ids = wp_cli("post", "list", "--post_type=attachment", f"--name={name}", "--field=ID").split()
    return ids[0] if ids else ""


def migrate_post(post_id: str, content: str) -> str | None:
    names = body_photos(content)
    if not names:
        return None
    keep = photo.meta(post_id, photo.PHOTO_META)
    if not keep:
        for name in names:
            att = _attachment_id(name)
            if att:
                photo.set_featured(post_id, att, name)
                photo._delete_meta(post_id, photo.PENDING_META)
                keep = name
                break
    wp_cli("post", "update", post_id, f"--post_content={strip_photos(content)}")
    for name in names:
        if name != keep:
            images.delete_attachment(name)
    return f"글 {post_id}: 본문 사진 {len(names)}장 정리, 대표 사진 {keep or '없음(제목 카드 유지)'}"


def main() -> None:
    ids = wp_cli("post", "list", "--post_type=post", "--post_status=any", "--field=ID").split()
    for post_id in ids:
        msg = migrate_post(post_id, wp_cli("post", "get", post_id, "--field=post_content"))
        if msg:
            print(f"[대표 사진] {msg}", flush=True)


if __name__ == "__main__":
    main()
