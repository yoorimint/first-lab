"""워드프레스에 글 저장 (WP-CLI): 블록 형식 변환 + 대표 이미지(제목 카드) + 본문 AI 이미지."""
from __future__ import annotations

from . import images
from .blocks import to_blocks
from .categories import slug_of
from .settings import wp_cli
from .writer import Article

SITE_URL = "https://first-lab.kr"


def _insert_images(content: str, blocks: list[str]) -> str:
    """이미지들을 소제목 앞에 고르게 나눠 넣기 (첫 이미지는 첫 소제목 앞 = 도입부 다음)."""
    marker = "<!-- wp:heading"
    positions, i = [], content.find(marker)
    while i != -1:
        positions.append(i)
        i = content.find(marker, i + 1)
    if not positions:
        return "\n\n".join(blocks) + "\n\n" + content
    step = max(1, len(positions) // len(blocks))
    chosen = [positions[min(k * step, len(positions) - 1)] for k in range(len(blocks))]
    for pos, block in sorted(zip(chosen, blocks), reverse=True):  # 뒤에서부터 넣어야 위치가 안 밀림
        content = content[:pos] + block + "\n\n" + content[pos:]
    return content


def build_content(article: Article, keyword: str, cloudflare: tuple[str, str] = ("", ""),
                  count: int = 3) -> tuple[str, str]:
    """(블록 본문, 메모). 이미지 실패는 글 저장을 막지 않는다. cloudflare=(계정 ID, API 토큰)"""
    content = to_blocks(article.content_html)
    account_id, token = cloudflare
    prompts = article.image_prompts[:count]
    if not (account_id and token and prompts):
        return content, "본문 이미지 없음"
    blocks, errors = [], []
    for n, prompt in enumerate(prompts, 1):
        alt = article.image_alts[n - 1] if n <= len(article.image_alts) and article.image_alts[n - 1] else keyword
        try:
            path = images.cloudflare_image(account_id, token, prompt)
            _, url = images.import_media(path, alt, alt)
            blocks.append(images.image_block(url, alt))
        except Exception as e:  # noqa: BLE001
            errors.append(str(e)[:80])
            print(f"[이미지] {n}번 실패: {e}", flush=True)
    if blocks:
        content = _insert_images(content, blocks)
    note = f"AI 이미지 {len(blocks)}장" + (f" (실패 {len(errors)})" if errors else "")
    return content, note


def _set_category(post_id: int, name: str) -> None:
    """카테고리 지정: 주소용 이름(slug)으로, 안 되면 이름으로 (관리자 화면에서 주소를 바꾼 경우 대비)."""
    import subprocess

    try:
        wp_cli("post", "term", "set", str(post_id), "category", slug_of(name), "--by=slug")
    except subprocess.CalledProcessError:
        try:
            wp_cli("post", "term", "set", str(post_id), "category", name)
        except subprocess.CalledProcessError as e:
            print(f"[카테고리] 지정 실패 ({name}): {e.stderr}", flush=True)


CARD_VERSION = "2"  # 제목 카드 디자인 버전 (바꾸면 배포 때 기존 글 카드도 다시 생성)


def set_featured_card(post_id: int, title: str, keyword: str) -> None:
    card = images.title_card(title, keyword)
    att_id, _ = images.import_media(card, title, title)
    wp_cli("post", "meta", "update", str(post_id), "_thumbnail_id", str(att_id))
    wp_cli("post", "meta", "update", str(post_id), "_firstlab_card", CARD_VERSION)
    wp_cli("post", "meta", "update", str(post_id), "_firstlab_card_title", title)  # 제목이 바뀌면 다시 만들기 위해


def site_author_id() -> str:
    """글 작성자로 쓸 계정 (첫 관리자, 화면 표시 이름은 wp-setup.sh 가 '퍼스트랩'으로 설정). 없으면 ''."""
    ids = wp_cli("user", "list", "--role=administrator", "--orderby=ID", "--order=ASC", "--field=ID").split()
    return ids[0] if ids else ""


def publish(article: Article, status: str, slug: str = "",
            cloudflare: tuple[str, str] = ("", ""), image_count: int = 3) -> tuple[int, str, str]:
    """slug: 글 주소(키워드). 짧은 키워드 주소는 SEO 에 유리하고, 서치콘솔 성과를 글과 연결하는 데 쓴다."""
    content, image_note = build_content(article, slug or article.title, cloudflare, image_count)
    post_id = int(wp_cli(
        "post", "create",
        f"--post_title={article.title}",
        *([f"--post_name={slug}"] if slug else []),
        f"--post_content={content}",
        f"--post_excerpt={article.excerpt}",
        f"--post_status={status}",
        *([f"--post_author={author}"] if (author := site_author_id()) else []),  # 비우면 작성자 없음(0)으로 저장돼 화면에 빈칸
        f"--tags_input={','.join(article.tags)}",
        "--porcelain",
    ).strip())
    _set_category(post_id, article.category)
    try:
        set_featured_card(post_id, article.title, slug or article.title)
    except Exception as e:  # noqa: BLE001 - 대표 이미지 실패가 글 저장을 막지 않게
        print(f"[이미지] 대표 이미지 실패: {e}", flush=True)
    if status == "publish":
        link = wp_cli("eval", f"echo get_permalink({post_id});").strip()
    else:  # 초안은 공개 주소가 없으므로 편집 화면 주소
        link = f"{SITE_URL}/wp-admin/post.php?post={post_id}&action=edit"
    return post_id, link, image_note
