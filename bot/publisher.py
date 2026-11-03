"""워드프레스에 글 저장 (WP-CLI): 블록 형식 변환 + 대표 이미지 + 본문 사진."""
from __future__ import annotations

from . import images
from .blocks import to_blocks
from .settings import wp_cli
from .writer import Article

SITE_URL = "https://first-lab.kr"


def _insert_photo(content: str, block: str) -> str:
    """첫 번째 소제목 바로 앞에 사진 넣기 (도입부 다음)."""
    marker = "<!-- wp:heading"
    i = content.find(marker)
    return content[:i] + block + "\n\n" + content[i:] if i > 0 else block + "\n\n" + content


def build_content(article: Article, keyword: str, pexels_key: str = "") -> tuple[str, str]:
    """(블록 본문, 메모). 사진 실패는 글 저장을 막지 않는다."""
    content = to_blocks(article.content_html)
    note = ""
    if pexels_key and article.image_query:
        try:
            photo = images.pexels_photo(pexels_key, article.image_query)
            if photo:
                _, url = images.import_media(photo["path"], keyword, keyword)
                caption = f'사진: <a href="{photo["url"]}">{photo["photographer"]} / Pexels</a>'
                content = _insert_photo(content, images.image_block(url, keyword, caption))
                note = "사진 1장"
        except Exception as e:  # noqa: BLE001
            note = f"사진 실패({e})"
    return content, note


def set_featured_card(post_id: int, title: str, keyword: str) -> None:
    card = images.title_card(title, keyword)
    att_id, _ = images.import_media(card, title, title)
    wp_cli("post", "meta", "update", str(post_id), "_thumbnail_id", str(att_id))


def publish(article: Article, status: str, slug: str = "", pexels_key: str = "") -> tuple[int, str]:
    """slug: 글 주소(키워드). 짧은 키워드 주소는 SEO 에 유리하고, 서치콘솔 성과를 글과 연결하는 데 쓴다."""
    content, _ = build_content(article, slug or article.title, pexels_key)
    post_id = int(wp_cli(
        "post", "create",
        f"--post_title={article.title}",
        *([f"--post_name={slug}"] if slug else []),
        f"--post_content={content}",
        f"--post_excerpt={article.excerpt}",
        f"--post_status={status}",
        f"--tags_input={','.join(article.tags)}",
        "--porcelain",
    ).strip())
    try:
        set_featured_card(post_id, article.title, slug or article.title)
    except Exception as e:  # noqa: BLE001 - 대표 이미지 실패가 글 저장을 막지 않게
        print(f"[이미지] 대표 이미지 실패: {e}", flush=True)
    if status == "publish":
        link = wp_cli("eval", f"echo get_permalink({post_id});").strip()
    else:  # 초안은 공개 주소가 없으므로 편집 화면 주소
        link = f"{SITE_URL}/wp-admin/post.php?post={post_id}&action=edit"
    return post_id, link
