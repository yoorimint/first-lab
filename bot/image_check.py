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

import json
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
    retry = False  # 새 장면이 글자 생기기 쉬움 → 사진은 그대로 두고 다음 검사에서 다른 장면을 받음
    for r in data.get("replace") or []:
        if r.get("old") == current:
            if rounds >= MAX_ROUND:
                remove = True  # 여러 번 바꿔도 계속 나쁨 → 뺌
            elif images.text_prone(r.get("prompt", "")):
                retry = True
            else:
                new_scene = r
    if retry and not new_scene and not remove:
        # 11/4 #168: 장면 단어 때문에 멀쩡히 바꿀 수 있던 사진을 빼 버린 일 → 횟수만 올리고 다시 검사 대상으로 둠
        wp_cli("post", "meta", "update", str(post_id), ROUND_META, str(rounds + 1))
        return f"글 {post_id} 새 장면이 글자 생기기 쉬움 → 다음 사진 검사에서 다른 장면으로"
    if not current and data.get("add"):
        new_scene = photo.first_scene([a.get("prompt", "") for a in data["add"]], [a.get("alt", "") for a in data["add"]])

    if remove:
        _to_title_card(post_id, current)
    elif new_scene:
        s = load_settings()
        try:
            wp_cli("post", "meta", "update", str(post_id), ROUND_META, str(rounds + 1))  # 실패해도 횟수는 올림 (11/5 검토: 끝없이 다시 시도)
            photo.make(post_id, new_scene, (s.cf_account_id, s.cf_api_token))  # 다른 오류면 예외 → 다음 배포 때 다시
        except images.QuotaExceeded:
            # 한도 소진은 사진 탓이 아님 → 횟수를 되돌림 (11/7 #1042·#1030·#1006: 한도 소진 2번을 '2번 실패'로 세어 사진을 영영 포기)
            wp_cli("post", "meta", "update", str(post_id), ROUND_META, str(rounds))
            # 한도 소진: 사진 채우기 대기로 넘김 (대기 중인 글은 검사 목록에서 빠져 같은 요청이 쌓이지 않음)
            wait = {"prompt": new_scene["prompt"], "alt": new_scene.get("alt", ""), "replace": current}
            wp_cli("post", "meta", "update", str(post_id), photo.PENDING_META, json.dumps([wait], ensure_ascii=False))
            return f"글 {post_id} 대표 사진 {'교체' if current else '추가'}: 한도 소진 → 오전 9시 이후 자동"
        return f"글 {post_id} 대표 사진 {'교체' if current else '추가'} → 새 사진 다시 검사"
    if not current and not remove:  # 대표 사진이 없는 글은 검사 끝으로 치지 않음 → 다음 검사 때 add 로 사진을 받게
        if images.quota_blocked():  # 한도 소진 중에는 사진을 못 만드는 게 당연 → 횟수를 세지 않고 다음 검사로
            return f"글 {post_id} 대표 사진 없음 (사진 한도 소진 중) → 오전 9시 뒤 다음 사진 검사에서 추가"
        if rounds >= MAX_ROUND:  # 여러 번 해도 사진이 안 생기면 제목 카드로 두고 끝 (11/5 검토: #176 검사 파일 8개)
            wp_cli("post", "meta", "update", str(post_id), CHECKED_META, "1")
            return f"글 {post_id} 사진을 {rounds}번 못 넣음 → 제목 카드로 두고 검사 끝"
        wp_cli("post", "meta", "update", str(post_id), ROUND_META, str(rounds + 1))
        return f"글 {post_id} 대표 사진 없음 → 다음 사진 검사에서 추가"
    wp_cli("post", "meta", "update", str(post_id), CHECKED_META, "1")
    return f"글 {post_id} 사진 검사 끝" + (" (사진 빼고 제목 카드)" if remove else " (이상 없음)")
