"""점검용: 글 본문을 텍스트로 출력. 사용: python -m bot.export_post [글ID ...] (생략 시 최근 초안 2개)"""
import html
import json
import re
import sys

from .settings import wp_cli


def to_text(content: str) -> str:
    content = re.sub(r"<!--.*?-->", "", content, flags=re.S)
    content = re.sub(r"<img[^>]*alt=\"([^\"]*)\"[^>]*>", r"[이미지: \1]", content)
    content = re.sub(r"<a [^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"\2(링크:\1)", content)
    content = re.sub(r"</(p|h2|h3|li|tr|figure)>", "\n", content)
    content = re.sub(r"<h2[^>]*>", "\n## ", content)
    content = re.sub(r"<h3[^>]*>", "\n### ", content)
    content = re.sub(r"<li[^>]*>", "- ", content)
    content = re.sub(r"</t[dh]>", " | ", content)
    content = re.sub(r"<[^>]+>", "", content)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(content)).strip()


def main() -> None:
    ids = sys.argv[1:] or json.loads(wp_cli("post", "list", "--post_status=draft", "--posts_per_page=2",
                                            "--orderby=ID", "--order=DESC", "--field=ID", "--format=json"))
    for pid in ids:
        title = wp_cli("post", "get", str(pid), "--field=post_title").strip()
        content = wp_cli("post", "get", str(pid), "--field=post_content")
        excerpt = wp_cli("post", "get", str(pid), "--field=post_excerpt").strip()
        print(f"\n===== #{pid} {title} =====\n요약: {excerpt}\n글자 수: {len(to_text(content))}\n")
        print(to_text(content))


if __name__ == "__main__":
    main()
