"""워드프레스에 글 저장 (WP-CLI): 블록 형식 변환 + 대표 사진(AI 1장, 실패하면 제목 카드). 본문에는 사진을 넣지 않는다."""
from __future__ import annotations

import re

from . import images, photo
from .blocks import to_blocks
from .categories import slug_of
from .schedule import mark_ready
from .settings import wp_cli
from .writer import Article

SITE_URL = "https://first-lab.kr"
STATUS_LABEL = {"publish": "발행", "draft": "초안 저장", "schedule": "공개 대기"}


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


_FIND_PHP = ('$p = get_page_by_path( sanitize_title( base64_decode( "%s" ) ), OBJECT, "post" ); '
             'echo $p ? $p->ID : "";')


def existing_post_id(slug: str) -> str:
    """이 주소(키워드)로 이미 있는 글 번호 (휴지통 제외 모든 상태). 없으면 ''."""
    import base64

    key = base64.b64encode(slug.strip().encode()).decode()
    return wp_cli("eval", _FIND_PHP % key).strip()


SIMILAR_RATIO = 0.85  # 키워드가 이만큼 비슷하면 같은 주제로 봄 ('대부업체대출조회'·'대부업대출조회' 0.93, '해외여행준비물'·'유럽여행준비물' 0.8)


def similar_post(keyword: str) -> tuple[str, str]:
    """키워드가 거의 같은 기존 글 (글 번호, 주소 키워드). 없으면 ('', '')."""
    import json
    import urllib.parse
    from difflib import SequenceMatcher

    key = keyword.replace(" ", "")
    posts = json.loads(wp_cli("post", "list", "--post_type=post", "--post_status=publish,future,draft",
                              "--fields=ID,post_name", "--format=json") or "[]")
    digits = re.findall(r"\d+", key)
    for p in posts:
        slug = urllib.parse.unquote(p["post_name"]).replace("-", "")
        # 숫자가 다르면 다른 주제 ('2026최저임금'↔'2027최저임금', '1주택자'↔'2주택자' — 11/5 검토)
        if slug and re.findall(r"\d+", slug) == digits and SequenceMatcher(None, key, slug).ratio() >= SIMILAR_RATIO:
            return str(p["ID"]), slug
    return "", ""


def site_author_id() -> str:
    """글 작성자로 쓸 계정 (첫 관리자, 화면 표시 이름은 wp-setup.sh 가 '퍼스트랩'으로 설정). 없으면 ''."""
    ids = wp_cli("user", "list", "--role=administrator", "--orderby=ID", "--order=ASC", "--field=ID").split()
    return ids[0] if ids else ""


def publish(article: Article, status: str, slug: str = "",
            cloudflare: tuple[str, str] = ("", "")) -> tuple[int, str, str]:
    """status: publish | draft | schedule (초안으로 저장 + 공개 대기열, schedule.py 가 공개 시각마다 오래된 것부터 공개)
    slug: 글 주소(키워드). 짧은 키워드 주소는 SEO 에 유리하고, 서치콘솔 성과를 글과 연결하는 데 쓴다.
    반환: (글 번호, 주소, 처리 메모)"""
    content = to_blocks(article.content_html)
    queued = status == "schedule"
    if queued:
        status = "draft"
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
    if queued:
        mark_ready(post_id)
    scene = photo.first_scene(article.image_prompts, article.image_alts)
    image_note = photo.make_or_wait(post_id, scene, cloudflare)
    if not photo.meta(post_id, photo.PHOTO_META):  # 사진이 없으면 우선 제목 카드
        try:
            set_featured_card(post_id, article.title, slug or article.title)
        except Exception as e:  # noqa: BLE001 - 대표 이미지 실패가 글 저장을 막지 않게
            print(f"[이미지] 제목 카드 실패: {e}", flush=True)
    if status == "publish":
        link = wp_cli("eval", f"echo get_permalink({post_id});").strip()
    else:  # 초안은 아직 공개 주소가 없으므로 편집 화면 주소
        link = f"{SITE_URL}/wp-admin/post.php?post={post_id}&action=edit"
    return post_id, link, image_note
