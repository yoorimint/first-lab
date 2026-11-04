"""AI 사진 검사 결과 반영: 글쓰기 작업(Claude)이 사진을 직접 보고 엉터리 글자·깨진 장면을 찾으면
서버가 그 사진을 새로 만들어 바꾸거나 뺀다. 운영자·감시자 손 없이 돈다.

파일: {"type": "image_check", "post_id", "replace": [{"old": "ai-…", "prompt", "alt", "why"}], "remove": ["ai-…"],
       "add": [{"prompt", "alt"}]}
  add: AI 사진이 하나도 없는 글(생성 실패 등)에 새로 넣을 사진 (사진이 이미 있으면 무시)
  replace·remove·add 가 모두 비면 → 이 글 사진은 검사 끝 (CHECKED_META = 1)
  바꾼 사진이 있으면 → 새 사진도 다시 검사받도록 미검사로 두고 ROUND_META +1.
  MAX_ROUND 번 바꿨는데도 또 나쁘면 바꾸지 않고 뺀다 (무한 반복 방지).
검사할 글 목록은 읽기 API 의 image_checks (wordpress/mu-plugins/first-lab-api.php).
"""
from __future__ import annotations

import re
import subprocess

from . import images
from .fix_images import _delete_attachment, _pattern, swap, uses
from .settings import load_settings, wp_cli

CHECKED_META = "_firstlab_img_checked"
ROUND_META = "_firstlab_img_round"
MAX_ROUND = 2


def remove_image(content: str, old: str) -> str:
    """old 사진이 든 이미지 블록(<!-- wp:image --> … <!-- /wp:image -->)을 통째로 뺀다."""
    block = re.compile(r"<!-- wp:image\b(?:(?!<!-- /wp:image -->).)*?" + _pattern(old).pattern
                       + r".*?<!-- /wp:image -->\s*", re.S)
    return block.sub("", content)


def _add_images(content: str, add: list[dict]) -> str:
    """사진 없는 글에 새 사진 넣기. 하나라도 실패하면 아무것도 넣지 않음 (다시 시도할 때 중복 방지)."""
    from .publisher import _insert_images

    s = load_settings()
    blocks = []
    for a in add[:3]:
        path = images.cloudflare_image(s.cf_account_id, s.cf_api_token, a["prompt"])
        _, url = images.import_media(path, a.get("alt") or "AI 사진", a.get("alt", ""))
        blocks.append(images.image_block(url, a.get("alt", "")))
    return _insert_images(content, blocks)


def _meta(post_id: int, key: str) -> str:
    try:
        return wp_cli("post", "meta", "get", str(post_id), key).strip()
    except subprocess.CalledProcessError:
        return ""


def apply_check(data: dict) -> str:
    post_id = int(data["post_id"])
    content = wp_cli("post", "get", str(post_id), "--field=post_content")
    rounds = int(_meta(post_id, ROUND_META) or 0)
    replace = [r for r in data.get("replace") or [] if uses(content, r["old"])]
    remove = [o for o in data.get("remove") or [] if uses(content, o)]
    # 이미 MAX_ROUND 번 바꿨거나, 새 장면도 글자가 생기기 쉬우면 바꾸지 않고 뺀다
    for r in list(replace):
        if rounds >= MAX_ROUND or images.text_prone(r["prompt"]):
            replace.remove(r)
            remove.append(r["old"])

    add = [a for a in data.get("add") or [] if not images.text_prone(a["prompt"])]
    if add and "/ai-" in content:
        add = []  # 이미 사진이 있으면 넣지 않음

    if data.get("add") and not add and "/ai-" not in content:
        return f"글 {post_id} 새 사진 장면이 모두 글자 생기기 쉬운 장면 → 넣지 않음 (다음 검사 때 다시)"

    done, failed = [], []
    if add:
        try:
            content = _add_images(content, add)
            done.append(f"추가 {len(add[:3])}장")
        except Exception as e:  # noqa: BLE001
            failed.append(f"사진 추가 ({str(e)[:60]})")
    if replace:
        s = load_settings()
        for r in replace:
            try:
                path = images.cloudflare_image(s.cf_account_id, s.cf_api_token, r["prompt"])
                _, new_url = images.import_media(path, r.get("alt") or r["old"], r.get("alt", ""))
            except Exception as e:  # noqa: BLE001
                failed.append(f"{r['old']} ({str(e)[:60]})")
                continue
            content = swap(content, r["old"], new_url, r.get("alt", ""))
            done.append(r["old"])
    for old in remove:
        content = remove_image(content, old)
    if done or remove:
        wp_cli("post", "update", str(post_id), f"--post_content={content}")
        for old in [d for d in done if d.startswith("ai-")] + remove:
            try:
                _delete_attachment(old)
            except subprocess.CalledProcessError:
                pass
    if failed:  # 큐 파일을 완료 처리하지 않음 → 다음 배포 때 남은 것만 다시 시도
        raise RuntimeError(f"글 {post_id} 사진 생성 실패: {', '.join(failed)}")
    if done:
        wp_cli("post", "meta", "update", str(post_id), ROUND_META, str(rounds + 1))
        return f"글 {post_id} 사진 {', '.join(done)} 교체·추가, {len(remove)}장 뺌 → 새 사진 다시 검사"
    wp_cli("post", "meta", "update", str(post_id), CHECKED_META, "1")
    return f"글 {post_id} 사진 검사 끝" + (f" ({len(remove)}장 뺌)" if remove else " (이상 없음)")
