"""대표 사진: 글마다 AI 사진 1장을 대표 이미지로 (홈 목록·공유 미리보기·구글 디스커버). 본문에는 사진을 넣지 않는다.

만들기 실패(한도 소진 등)면 제목 카드가 대표 이미지로 남고, 한도 소진이면 장면 설명을 PENDING_META 에 적어 둔다
→ 한도가 초기화되면 pending_images.py 가 채운다. 사진 검사·교체는 image_check.py.
"""
from __future__ import annotations

import json
import subprocess

from . import images
from .settings import wp_cli

PHOTO_META = "_firstlab_photo"           # 대표 사진 파일 이름 (ai-xxxxxxxxxx)
PENDING_META = "_firstlab_img_pending"   # 한도 소진으로 못 만든 장면 [{"prompt", "alt"}]
CARD_METAS = ("_firstlab_card", "_firstlab_card_title")  # 제목 카드 표시 (있으면 fix_posts 가 카드를 다시 만듦)


def _delete_meta(post_id: int | str, key: str) -> None:
    try:
        wp_cli("post", "meta", "delete", str(post_id), key)
    except subprocess.CalledProcessError:
        pass  # 원래 없음


def meta(post_id: int | str, key: str) -> str:
    try:
        return wp_cli("post", "meta", "get", str(post_id), key).strip()
    except subprocess.CalledProcessError:
        return ""


def first_scene(prompts: list[str], alts: list[str]) -> dict | None:
    """글자가 생기기 쉬운 장면을 뺀 첫 장면."""
    for n, prompt in enumerate(prompts):
        if prompt and not images.text_prone(prompt):
            return {"prompt": prompt, "alt": alts[n] if n < len(alts) else ""}
    return None


def set_featured(post_id: int | str, att_id: int | str, name: str) -> None:
    wp_cli("post", "meta", "update", str(post_id), "_thumbnail_id", str(att_id))
    wp_cli("post", "meta", "update", str(post_id), PHOTO_META, name)
    for key in CARD_METAS:
        _delete_meta(post_id, key)


def make(post_id: int | str, scene: dict, cloudflare: tuple[str, str]) -> str:
    """AI 사진을 만들어 대표 이미지로. 반환: 사진 파일 이름. 한도 소진이면 images.QuotaExceeded."""
    path = images.cloudflare_image(*cloudflare, scene["prompt"])
    name = path.stem
    att_id, _ = images.import_media(path, scene.get("alt") or name, scene.get("alt", ""))
    old = meta(post_id, PHOTO_META)
    set_featured(post_id, att_id, name)
    if old and old != name:
        images.delete_attachment(old)
    return name


def make_or_wait(post_id: int, scene: dict | None, cloudflare: tuple[str, str]) -> str:
    """새 글용: 만들고, 한도 소진이면 나중에 채우도록 적어 둔다. 반환: 처리 메모."""
    if not scene:
        return "대표 사진 없음 (장면 없음·글자 장면)"
    if not all(cloudflare):
        return "대표 사진 없음 (Cloudflare 설정 없음)"
    try:
        make(post_id, scene, cloudflare)
        return "대표 사진 AI 1장"
    except images.QuotaExceeded:
        wp_cli("post", "meta", "update", str(post_id), PENDING_META, json.dumps([scene], ensure_ascii=False))
        return "대표 사진: 한도 소진 → 오전 9시 이후 자동 추가"
    except Exception as e:  # noqa: BLE001 - 사진 실패가 글 저장을 막지 않게 (사진 검사가 나중에 채움)
        print(f"[이미지] 대표 사진 실패: {e}", flush=True)
        return "대표 사진 실패 (사진 검사 때 다시)"
