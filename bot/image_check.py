"""대표 사진 검사 결과 반영: 글쓰기 작업(Claude)이 대표 사진을 직접 보고 엉터리 글자·깨진 사진이면
서버가 새로 만들어 바꾸거나, 사진이 없는 글엔 새로 넣는다. 운영자·감시자 손 없이 돈다.

파일: {"type": "image_check", "post_id", "replace": [{"old": "ai-…", "prompt", "alt", "why"}], "remove": ["ai-…"],
       "add": [{"prompt", "alt"}]}
  replace: 지금 대표 사진(old)을 새 장면으로 / remove: 대표 사진을 빼고 제목 카드로 / add: 대표 사진이 없을 때 새로
  셋 다 비면 → 검사 끝 (CHECKED_META = 1). 새로 만든 사진은 다시 검사받도록 ROUND_META +1.
  MAX_ROUND 번 바꿨는데도 또 나쁘면 바꾸지 않고 뺀다 (무한 반복 방지).
검사할 글 목록은 읽기 API 의 image_checks (wordpress/mu-plugins/first-lab-api.php).
"""
from __future__ import annotations

import urllib.parse

from . import images, photo
from .settings import load_settings, wp_cli

CHECKED_META = "_firstlab_img_checked"
ROUND_META = "_firstlab_img_round"
MAX_ROUND = 2


def _to_title_card(post_id: int, old: str) -> None:
    from .publisher import set_featured_card

    title = wp_cli("post", "get", str(post_id), "--field=post_title").strip()
    slug = urllib.parse.unquote(wp_cli("post", "get", str(post_id), "--field=post_name").strip())
    set_featured_card(post_id, title, slug or title)
    photo._delete_meta(post_id, photo.PHOTO_META)
    images.delete_attachment(old)


def apply_check(data: dict) -> str:
    post_id = int(data["post_id"])
    current = photo.meta(post_id, photo.PHOTO_META)
    rounds = int(photo.meta(post_id, ROUND_META) or 0)
    new_scene = None
    remove = bool(current) and current in (data.get("remove") or [])
    for r in data.get("replace") or []:
        if r.get("old") == current:
            if rounds >= MAX_ROUND or images.text_prone(r.get("prompt", "")):
                remove = True  # 바꿔도 계속 나쁘거나 새 장면도 글자 생기기 쉬움 → 뺌
            else:
                new_scene = r
    if not current and data.get("add"):
        new_scene = photo.first_scene([a.get("prompt", "") for a in data["add"]], [a.get("alt", "") for a in data["add"]])

    if remove:
        _to_title_card(post_id, current)
    elif new_scene:
        s = load_settings()
        photo.make(post_id, new_scene, (s.cf_account_id, s.cf_api_token))  # 실패하면 예외 → 큐 파일을 다음 배포 때 다시
        wp_cli("post", "meta", "update", str(post_id), ROUND_META, str(rounds + 1))
        return f"글 {post_id} 대표 사진 {'교체' if current else '추가'} → 새 사진 다시 검사"
    wp_cli("post", "meta", "update", str(post_id), CHECKED_META, "1")
    return f"글 {post_id} 사진 검사 끝" + (" (사진 빼고 제목 카드)" if remove else " (이상 없음)")
