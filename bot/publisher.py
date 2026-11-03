"""워드프레스에 글 저장 (WP-CLI): 블록 형식 변환 + 대표 이미지(제목 카드) + 본문 AI 이미지."""
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


def build_content(article: Article, keyword: str, cloudflare: tuple[str, str] = ("", "")) -> tuple[str, str]:
    """(블록 본문, 메모). 이미지 실패는 글 저장을 막지 않는다. cloudflare=(계정 ID, API 토큰)"""
    content = to_blocks(article.content_html)
    account_id, token = cloudflare
    if not (account_id and token and article.image_query):
        return content, "본문 이미지 없음(Cloudflare 미설정)"
    try:
        path = images.cloudflare_image(account_id, token, article.image_query)
        _, url = images.import_media(path, keyword, keyword)
        content = _insert_photo(content, images.image_block(url, keyword, "AI 생성 이미지"))
        return content, "AI 이미지 1장"
    except Exception as e:  # noqa: BLE001
        print(f"[이미지] 본문 이미지 실패: {e}", flush=True)
        return content, f"본문 이미지 실패({e})"


def set_featured_card(post_id: int, title: str, keyword: str) -> None:
    card = images.title_card(title, keyword)
    att_id, _ = images.import_media(card, title, title)
    wp_cli("post", "meta", "update", str(post_id), "_thumbnail_id", str(att_id))


def publish(article: Article, status: str, slug: str = "",
            cloudflare: tuple[str, str] = ("", "")) -> tuple[int, str, str]:
    """slug: 글 주소(키워드). 짧은 키워드 주소는 SEO 에 유리하고, 서치콘솔 성과를 글과 연결하는 데 쓴다."""
    content, image_note = build_content(article, slug or article.title, cloudflare)
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
    return post_id, link, image_note
