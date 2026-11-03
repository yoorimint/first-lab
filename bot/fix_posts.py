"""이미 저장된 글 보정 (배포 때 자동 실행, 여러 번 실행해도 안전).

  - 일반 HTML 본문 → 블록 형식 (앱 편집기에서 표가 '?'로 보이던 문제)
  - 대표 이미지가 없거나 예전 디자인의 제목 카드면 새로 생성 (직접 올린 이미지는 유지)
  - 예전 'AI 생성 이미지' 캡션 제거
"""
import json
import subprocess
import urllib.parse

from .blocks import to_blocks
from .publisher import CARD_VERSION, set_featured_card
from .settings import wp_cli


def _meta(pid: str, key: str) -> str:
    try:
        return wp_cli("post", "meta", "get", pid, key).strip()
    except subprocess.CalledProcessError:  # 값이 없으면 WP-CLI 가 오류로 끝남
        return ""


def _is_bot_card(att_id: str) -> bool:
    """봇이 만든 제목 카드인지 (직접 올린 이미지는 건드리지 않음)."""
    return "/card-" in wp_cli("eval", f"echo wp_get_attachment_url({int(att_id)});")


def main() -> None:
    posts = json.loads(wp_cli("post", "list", "--post_type=post", "--post_status=any",
                              "--fields=ID,post_title,post_name", "--format=json") or "[]")
    for p in posts:
        pid = str(p["ID"])
        content = wp_cli("post", "get", pid, "--field=post_content")
        if "AI 생성 이미지</figcaption>" in content:  # 예전 캡션 제거 (운영자 요청)
            content = content.replace('<figcaption class="wp-element-caption">AI 생성 이미지</figcaption>', "")
            wp_cli("post", "update", pid, f"--post_content={content}")
            print(f"[보정] {pid} 이미지 캡션 제거")
        if "<!-- wp:" not in content and "<h2" in content:
            wp_cli("post", "update", pid, f"--post_content={to_blocks(content)}")
            print(f"[보정] {pid} 블록 형식 변환")
        else:
            print(f"[보정] {pid} 블록 형식 (이미 됨)")
        thumb, card = _meta(pid, "_thumbnail_id"), _meta(pid, "_firstlab_card")
        if not thumb or (card != CARD_VERSION and (card or _is_bot_card(thumb))):
            keyword = urllib.parse.unquote(p["post_name"]) or p["post_title"]
            set_featured_card(int(pid), p["post_title"], keyword)
            print(f"[보정] {pid} 대표 이미지(제목 카드 v{CARD_VERSION})")


if __name__ == "__main__":
    main()
