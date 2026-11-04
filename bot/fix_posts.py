"""이미 저장된 글 보정 (배포 때 자동 실행, 여러 번 실행해도 안전).

  - 일반 HTML 본문 → 블록 형식 (앱 편집기에서 표가 '?'로 보이던 문제)
  - 대표 이미지가 없거나, 봇 제목 카드가 옛 디자인이거나 글 제목이 바뀌었으면 새로 생성 (직접 올린 이미지는 유지)
  - 예전 'AI 생성 이미지' 캡션 제거
"""
import json
import re
import subprocess
import urllib.parse
from pathlib import Path

from .blocks import to_blocks
from .publisher import CARD_VERSION, set_featured_card, site_author_id
from .settings import wp_cli


def _meta(pid: str, key: str) -> str:
    try:
        return wp_cli("post", "meta", "get", pid, key).strip()
    except subprocess.CalledProcessError:  # 값이 없으면 WP-CLI 가 오류로 끝남
        return ""


def _is_bot_card(att_id: str) -> bool:
    """봇이 만든 제목 카드인지 (직접 올린 이미지는 건드리지 않음)."""
    return "/card-" in wp_cli("eval", f"echo wp_get_attachment_url({int(att_id)});")


def _classify_uncategorized(posts: list[dict]) -> None:
    """'미분류' 글을 AI 로 분류 (설정의 글쓰기 AI 사용)."""
    from .categories import CATEGORIES, slug_of
    from .llm import make_llm
    from .settings import load_settings

    targets = []
    for p in posts:
        cats = set(wp_cli("post", "term", "list", str(p["ID"]), "category", "--field=name").split())
        if cats <= {"미분류", "Uncategorized"}:  # 한국어 설치본은 기본 카테고리 이름이 '미분류'
            targets.append(p)
    if not targets:
        return
    s = load_settings()
    llm = make_llm(s.provider, s.api_key, s.model, light=True)
    listing = "\n".join(f"{p['ID']}: {p['post_title']}" for p in targets)
    raw = llm.generate(
        f"블로그 글 제목을 보고 카테고리를 하나씩 고른다. 카테고리: {' / '.join(CATEGORIES)}\n"
        '출력은 JSON 객체 하나만: {"글ID": "카테고리"}', listing)
    result = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    for pid, name in result.items():
        wp_cli("post", "term", "set", str(pid), "category", slug_of(str(name)), "--by=slug")
        print(f"[보정] {pid} 카테고리 → {name}")


PATCHES = Path(__file__).resolve().parent.parent / "wordpress" / "patches.json"


_PARA_END = "<!-- /wp:paragraph -->"


def patch_content(content: str, patch: dict) -> str | None:
    """'find' 가 들어 있는 문단(<p>…</p>)을 고친 본문. 고칠 게 없으면(이미 적용됨) None.

    replace_paragraph: 문단 전체를 이 문장으로 교체
    insert_after: 문단(블록) 바로 뒤에 이 내용을 블록으로 추가 (예: 계산기 [firstlab_calc id="..."])
    """
    # 문단 안에 <strong> 같은 태그가 있어도 찾도록: '<p>' 부터 같은 문단의 '</p>' 까지
    m = re.search(r"<p>(?:(?!</p>).)*?" + re.escape(patch["find"]) + r".*?</p>", content, flags=re.S)
    if "insert_after" in patch:
        if not m or patch["insert_after"] in content:
            return None
        end = content.find(_PARA_END, m.end())
        at = end + len(_PARA_END) if end != -1 and not content[m.end():end].strip() else m.end()
        return f"{content[:at]}\n\n{to_blocks('<p>' + patch['insert_after'] + '</p>')}{content[at:]}"
    if not m:
        return None
    return content.replace(m.group(0), f"<p>{patch['replace_paragraph']}</p>")


def apply_patches() -> None:
    """wordpress/patches.json 의 글 수정 적용. 'find' 가 들어 있는 문단 전체를 교체 (이미 바뀌었으면 건너뜀)."""
    if not PATCHES.exists():
        return
    for patch in json.loads(PATCHES.read_text(encoding="utf-8")):
        pid = str(patch["post"])
        try:
            content = wp_cli("post", "get", pid, "--field=post_content")
        except subprocess.CalledProcessError:
            continue
        content = patch_content(content, patch)
        if content is None:
            continue
        wp_cli("post", "update", pid, f"--post_content={content}")
        kind = "블록 추가" if "insert_after" in patch else "문단 수정"
        print(f"[보정] {pid} {kind}: {patch['why']}")


_SET_AUTHOR_PHP = r"""
global $wpdb;
$n = $wpdb->update( $wpdb->posts, array( 'post_author' => %d ), array( 'post_author' => 0, 'post_type' => 'post' ) );
wp_cache_flush();
echo (int) $n;
"""


def fill_missing_author() -> None:
    """작성자 없이(0) 저장된 글에 사이트 작성자 지정. DB 직접 수정 → '마지막 업데이트' 날짜는 바뀌지 않음."""
    author = site_author_id()
    if not author:
        return
    n = wp_cli("eval", _SET_AUTHOR_PHP % int(author)).strip()
    if n not in ("", "0"):
        print(f"[보정] 작성자 없는 글 {n}개 → 사이트 작성자 지정")


def main() -> None:
    apply_patches()
    fill_missing_author()
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
        sync_card(p)
    try:
        _classify_uncategorized(posts)
    except Exception as e:  # noqa: BLE001
        print(f"[보정] 카테고리 분류 실패: {e}")


def sync_card(p: dict) -> bool:
    """대표 이미지가 없거나, 봇이 만든 제목 카드인데 디자인이 옛 버전이거나 제목이 바뀌었으면 다시 생성."""
    pid = str(p["ID"])
    thumb, card = _meta(pid, "_thumbnail_id"), _meta(pid, "_firstlab_card")
    bot_card = bool(card) or (bool(thumb) and _is_bot_card(thumb))
    title_changed = bot_card and _meta(pid, "_firstlab_card_title") != p["post_title"]
    if not thumb or (bot_card and (card != CARD_VERSION or title_changed)):
        keyword = urllib.parse.unquote(p["post_name"]) or p["post_title"]
        set_featured_card(int(pid), p["post_title"], keyword)
        print(f"[보정] {pid} 대표 이미지(제목 카드 v{CARD_VERSION})")
        return True
    return False



if __name__ == "__main__":
    main()
