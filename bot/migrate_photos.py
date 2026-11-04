"""기존 글 정리 (배포 때 실행, 여러 번 실행해도 안전): 본문 AI 사진 → 대표 사진 1장으로.

본문 AI 사진 중 미디어에 실제로 있는 첫 장을 대표 이미지로 옮긴 뒤에만 본문 사진 블록을 뺀다
(대표 사진을 못 정하면 본문을 건드리지 않음). 대표 사진으로 쓰지 않는 사진은 미디어에서 지운다.
대표 사진 없이 본문 사진만 빠진 글(11/4 첫 정리 오류)은 글 수정 기록(revision)에서 사진을 찾아 되살린다.
"""
from __future__ import annotations

import re

from . import images, photo
from .settings import wp_cli

_AI_SRC = r'https?://[^"\s]*/(?P<name>ai-[0-9a-f]{10})(?:-\d+x\d+)?\.jpg'
# 사진 블록(wp:image)뿐 아니라 HTML 블록(wp:html)에 든 사진도 — 보류 수정본은 figure 가 HTML 블록으로 저장됨 (11/4 #163)
_BLOCK = re.compile(r"<!-- wp:(?P<kind>image|html)\b(?:(?!<!-- /wp:(?P=kind) -->).)*?" + _AI_SRC
                    + r".*?<!-- /wp:(?P=kind) -->\s*", re.S)
_ANY_SRC = re.compile(_AI_SRC)
SCANNED_META = "_firstlab_photo_scanned2"  # 수정 기록을 뒤져 봤는데 사진이 없던 글 (1차는 첨부 찾기 오류라 다시 확인)


def body_photos(content: str) -> list[str]:
    """본문 AI 사진 이름 (순서대로)."""
    return [m.group("name") for m in _BLOCK.finditer(content)]


def strip_photos(content: str) -> str:
    return _BLOCK.sub("", content)


def _pick(names: list[str]) -> tuple[str, str]:
    """미디어에 있는 첫 사진 (이름, 첨부 번호). 없으면 ('', '')."""
    for name in dict.fromkeys(names):  # 순서 유지 중복 제거
        att = images.attachment_id(name)
        if att:
            return name, att
    return "", ""


def _from_revisions(post_id: str) -> list[str]:
    """글 수정 기록(최신 것부터)에 남은 AI 사진 이름."""
    revs = wp_cli("post", "list", "--post_type=revision", "--post_status=inherit", f"--post_parent={post_id}",
                  "--orderby=ID", "--order=DESC", "--field=ID").split()
    for rev in revs:
        names = [m.group("name") for m in _ANY_SRC.finditer(wp_cli("post", "get", rev, "--field=post_content"))]
        if names:
            return names
    return []


def _feature(post_id: str, name: str, att: str) -> None:
    photo.set_featured(post_id, att, name)
    photo._delete_meta(post_id, photo.PENDING_META)  # 사진을 찾았으니 새로 만들 필요 없음


def migrate_post(post_id: str, content: str) -> str | None:
    names = body_photos(content)
    current = photo.meta(post_id, photo.PHOTO_META)
    if not names:
        if current or photo.meta(post_id, SCANNED_META):
            return None
        name, att = _pick(_from_revisions(post_id))  # 첫 정리 오류로 사진만 빠진 글 되살리기
        if not name:
            wp_cli("post", "meta", "update", post_id, SCANNED_META, "1")  # 다음 배포 때 다시 뒤지지 않게
            return None
        _feature(post_id, name, att)
        return f"글 {post_id}: 수정 기록에서 대표 사진 {name} 되살림"
    keep = current
    if not keep:
        keep, att = _pick(names)
        if not keep:
            return f"글 {post_id}: 본문 사진 {len(names)}장이 미디어에 없음 → 그대로 둠"
        _feature(post_id, keep, att)
    wp_cli("post", "update", post_id, f"--post_content={strip_photos(content)}")
    for name in names:
        if name != keep:
            images.delete_attachment(name)
    return f"글 {post_id}: 본문 사진 {len(names)}장 정리, 대표 사진 {keep}"


def main() -> None:
    ids = wp_cli("post", "list", "--post_type=post", "--post_status=any", "--field=ID").split()
    for post_id in ids:
        msg = migrate_post(post_id, wp_cli("post", "get", post_id, "--field=post_content"))
        if msg:
            print(f"[대표 사진] {msg}", flush=True)
    # 감시용 요약: '사진 없음'이 늘면 이상 (대기 = 한도 초기화 후 자동으로 채워질 글)
    has = [i for i in ids if photo.meta(i, photo.PHOTO_META)]
    waiting = [i for i in ids if i not in has and photo.meta(i, photo.PENDING_META)]
    none = [i for i in ids if i not in has and i not in waiting]
    from .image_check import CHECKED_META
    for i in none:  # 대표 사진이 없는데 '검사 끝'이면 사진을 영영 못 받음 → 다시 검사 대상으로
        if photo.meta(i, CHECKED_META):
            photo._delete_meta(i, CHECKED_META)
    left = [i for i in ids if _ANY_SRC.search(wp_cli("post", "get", i, "--field=post_content"))]  # 0 이어야 정상
    print(f"[대표 사진] 요약: 사진 {len(has)}개 · 대기 {len(waiting)}개 · 없음(제목 카드) {len(none)}개 {none[:15]}"
          f" · 본문에 AI 사진 남은 글 {len(left)}개 {left[:15]}", flush=True)


if __name__ == "__main__":
    main()
