"""워드프레스에 글 저장 (WP-CLI)."""
from __future__ import annotations

from .settings import wp_cli
from .writer import Article

SITE_URL = "https://first-lab.kr"


def publish(article: Article, status: str, slug: str = "") -> tuple[int, str]:
    """slug: 글 주소(키워드). 짧은 키워드 주소는 SEO 에 유리하고, 서치콘솔 성과를 글과 연결하는 데 쓴다."""
    post_id = int(wp_cli(
        "post", "create",
        f"--post_title={article.title}",
        *([f"--post_name={slug}"] if slug else []),
        f"--post_content={article.content_html}",
        f"--post_excerpt={article.excerpt}",
        f"--post_status={status}",
        f"--tags_input={','.join(article.tags)}",
        "--porcelain",
    ).strip())
    if status == "publish":
        link = wp_cli("eval", f"echo get_permalink({post_id});").strip()
    else:  # 초안은 공개 주소가 없으므로 편집 화면 주소
        link = f"{SITE_URL}/wp-admin/post.php?post={post_id}&action=edit"
    return post_id, link
