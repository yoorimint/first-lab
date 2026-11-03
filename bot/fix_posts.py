"""이미 저장된 글 보정 (배포 때 자동 실행, 여러 번 실행해도 안전).

  - 일반 HTML 본문 → 블록 형식 (앱 편집기에서 표가 '?'로 보이던 문제)
  - 대표 이미지가 없으면 제목 카드 생성
"""
import json
import urllib.parse

from .blocks import to_blocks
from .publisher import set_featured_card
from .settings import wp_cli


def main() -> None:
    posts = json.loads(wp_cli("post", "list", "--post_type=post", "--post_status=any",
                              "--fields=ID,post_title,post_name", "--format=json") or "[]")
    for p in posts:
        pid = str(p["ID"])
        content = wp_cli("post", "get", pid, "--field=post_content")
        if "<!-- wp:" not in content and "<h2" in content:
            wp_cli("post", "update", pid, f"--post_content={to_blocks(content)}")
            print(f"[보정] {pid} 블록 형식 변환")
        if not wp_cli("post", "meta", "get", pid, "_thumbnail_id").strip():
            keyword = urllib.parse.unquote(p["post_name"]) or p["post_title"]
            set_featured_card(int(pid), p["post_title"], keyword)
            print(f"[보정] {pid} 대표 이미지 추가")


if __name__ == "__main__":
    main()
