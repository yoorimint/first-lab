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

from .blocks import repair_lists, to_blocks
from .publisher import CARD_VERSION, set_featured_card, site_author_id
from . import wpbulk
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
# 글을 통째로 다시 쓰면(갱신·쉬운 말 다듬기) 그때까지의 패치는 새 글에 녹은 것으로 보고 다시 적용하지 않음.
# 값 = 다시 쓸 때의 patches.json 항목 수 → 이 번호보다 앞 패치는 건너뜀, 뒤에 생긴 패치(새 검수)만 적용.
# (11/10 #1775 등: 예전 패치가 다시 쓴 문단·'한 줄 정리'를 옛 문단으로 덮어씀. 운영자 '변경된 글 기준으로 고쳐야지')
UPTO_META = "_firstlab_patches_upto"
PLAIN_MARK = "<strong>한 줄 정리"  # 쉬운 말로 다시 쓴 글 표시 (bot/patch_check.PLAIN_MARK 와 같음)


def patch_count() -> int:
    return len(json.loads(PATCHES.read_text(encoding="utf-8"))) if PATCHES.exists() else 0


def _upto(pid: str, bulk: dict[str, dict] | None) -> int:
    raw = (bulk or {}).get(pid, {}).get("meta", {}).get(UPTO_META, "") if bulk is not None else _meta(pid, UPTO_META)
    return int(raw) if str(raw).isdigit() else 0


def _backfill_upto(bulk: dict[str, dict], total: int) -> None:
    """이 장치 전에 이미 다시 쓴 글(본문 '한 줄 정리' 또는 갱신 기록)에 한 번 표시 — 이번 배포의 패치 적용이 끝난 뒤라 안전."""
    for pid, b in bulk.items():
        meta = b.get("meta", {})
        if meta.get(UPTO_META) or not (PLAIN_MARK in (b.get("content") or "") or meta.get("_firstlab_refreshed")):
            continue
        wp_cli("post", "meta", "update", pid, UPTO_META, str(total))
        meta[UPTO_META] = str(total)


_PARA_END = "<!-- /wp:paragraph -->"


def patch_content(content: str, patch: dict) -> str | None:
    """'find' 가 들어 있는 문단(<p>…</p>)을 고친 본문. 고칠 게 없으면(이미 적용됨) None.

    replace_paragraph: 문단 전체를 이 문장으로 교체 ("tag": "li" 면 목록 항목 하나를 교체)
    insert_after: 문단(블록) 바로 뒤에 이 내용을 블록으로 추가 (예: 계산기 [firstlab_calc id="..."])
    remove_block: true 면 find 가 든 블록(<!-- wp:… --> … <!-- /wp:… -->) 통째로 삭제 (근거 없는 표 등)
    remove_element: true 면 find 가 든 요소(tag, 예: 표의 한 줄 tr)만 삭제
    """
    if patch.get("remove_block"):
        block = re.search(r"<!-- wp:(?P<kind>[a-z/-]+)\b[^>]*-->(?:(?!<!-- /wp:(?P=kind) -->).)*?"
                          + re.escape(patch["find"]) + r".*?<!-- /wp:(?P=kind) -->\s*", content, flags=re.S)
        return content.replace(block.group(0), "", 1) if block else None
    tag = patch.get("tag", "p")
    # 안에 <strong> 같은 태그가 있어도 찾도록: '<p>' 부터 같은 문단의 '</p>' 까지.
    # 여는 태그의 속성(<h2 class="wp-block-heading"> 등)은 그대로 둔다
    find = patch["find"]
    if find.rstrip().endswith(f"</{tag}>"):
        # find 가 닫는 태그로 끝나면 그 문단에서 끝냄 — 안 그러면 '.*?</p>' 가 다음 문단까지 삼킴 (11/5 #51·#59·#70 다음 문단이 사라짐)
        core = find.rstrip()[: -len(f"</{tag}>")]
        m = re.search(rf"<{tag}(?P<attrs>\s[^>]*)?>(?:(?!</{tag}>).)*?" + re.escape(core) + rf"</{tag}>", content, flags=re.S)
    else:
        m = re.search(rf"<{tag}(?P<attrs>\s[^>]*)?>(?:(?!</{tag}>).)*?" + re.escape(find) + rf"(?:(?!</{tag}>).)*?</{tag}>",
                      content, flags=re.S)
    if "insert_after" in patch:
        if not m or patch["insert_after"] in content:
            return None
        end = content.find(_PARA_END, m.end())
        at = end + len(_PARA_END) if end != -1 and not content[m.end():end].strip() else m.end()
        return f"{content[:at]}\n\n{to_blocks('<p>' + patch['insert_after'] + '</p>')}{content[at:]}"
    if not m:
        return None
    if patch.get("remove_element"):  # 그 요소(표의 한 줄, 목록 한 항목 등)만 삭제
        return content.replace(m.group(0), "", 1)
    return content.replace(m.group(0), f"<{tag}{m.group('attrs') or ''}>{inner(patch['replace_paragraph'], tag)}</{tag}>")


def inner(text: str, tag: str) -> str:
    """고칠 문장에 겉 태그('<p>…</p>')까지 적었으면 벗김 — 그대로 넣으면 '<p><p>…</p></p>' 이중 문단이 됨 (11/5 #578)."""
    m = re.fullmatch(rf"\s*<{tag}(?:\s[^>]*)?>(.*)</{tag}>\s*", text, flags=re.S)
    return m.group(1) if m and f"<{tag}" not in m.group(1) else text


def unnest(content: str) -> str:
    """이미 생긴 이중 문단 '<p><p>…</p></p>' → '<p>…</p>'."""
    return re.sub(r"<p>(\s*)<p>((?:(?!</p>).)*)</p>(\s*)</p>", r"<p>\2</p>", content, flags=re.S)


def apply_patches(bulk: dict[str, dict] | None = None) -> None:
    """wordpress/patches.json 의 글 수정 적용. 'find' 가 들어 있는 문단 전체를 교체 (이미 바뀌었으면 건너뜀).
    bulk: wpbulk.posts() 로 미리 읽은 글 (없으면 글마다 읽음). 고친 글은 bulk 에도 반영해 다음 패치가 이어서 본다."""
    if not PATCHES.exists():
        return
    patches = json.loads(PATCHES.read_text(encoding="utf-8"))
    for n, patch in enumerate(patches):
        pid = str(patch["post"])
        if "set_title" in patch or "set_excerpt" in patch:
            _patch_fields(pid, patch, bulk)
            continue
        if n < _upto(pid, bulk):  # 다시 쓰기 전 패치 → 새 글 기준이라 건너뜀
            continue
        if bulk is not None:
            if pid not in bulk:
                continue
            content = bulk[pid]["content"]
        else:
            try:
                content = wp_cli("post", "get", pid, "--field=post_content")
            except subprocess.CalledProcessError:
                continue
        updated = patch_content(content, patch)
        if updated is None and unnest(content) != content:  # 예전 패치가 만든 이중 문단 바로잡기
            updated = unnest(content)
        elif updated is not None:
            updated = unnest(updated)
        if updated is None:
            if "insert_after" in patch:  # 넣은 블록이 그대로 있는지 확인용 (편집기 저장으로 지워지면 다시 넣음)
                print(f"[확인] {pid} '{patch['insert_after']}' {content.count(patch['insert_after'])}개", flush=True)
            continue
        content = updated
        wp_cli("post", "update", pid, f"--post_content={content}")
        if bulk is not None:
            bulk[pid]["content"] = content
        kind = "블록 추가" if "insert_after" in patch else "문단 수정"
        print(f"[보정] {pid} {kind}: {patch['why']}")
    if bulk is not None:
        _backfill_upto(bulk, len(patches))


def _patch_fields(pid: str, patch: dict, bulk: dict[str, dict] | None = None) -> None:
    """제목·요약문 고치기: 지금 값이 from_title/from_excerpt 와 같을 때만 (나중에 다른 이유로 바뀐 값은 되돌리지 않음)."""
    for field, frm, to in (("post_title", "from_title", "set_title"), ("post_excerpt", "from_excerpt", "set_excerpt")):
        if to not in patch:
            continue
        short = "title" if field == "post_title" else "excerpt"
        if bulk is not None:
            if pid not in bulk:
                return
            current = str(bulk[pid][short]).strip()
        else:
            try:
                current = wp_cli("post", "get", pid, f"--field={field}").strip()
            except subprocess.CalledProcessError:
                return
        if current == patch.get(frm, "").strip() and current != patch[to]:
            wp_cli("post", "update", pid, f"--{field}={patch[to]}")
            if bulk is not None:
                bulk[pid][short] = patch[to]
            print(f"[보정] {pid} {'제목' if field == 'post_title' else '요약문'} 수정: {patch['why']}", flush=True)


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
    bulk = wpbulk.posts()  # 글 본문·메타를 한 번에 (글마다 wp-cli 를 부르면 배포가 10분을 넘김)
    apply_patches(bulk)
    fill_missing_author()
    posts = [{"ID": int(pid), "post_title": b["title"], "post_name": b["name"], "meta": b["meta"]} for pid, b in bulk.items()]
    for p in posts:
        pid = str(p["ID"])
        content = bulk[pid]["content"]
        if "AI 생성 이미지</figcaption>" in content:  # 예전 캡션 제거 (운영자 요청)
            content = content.replace('<figcaption class="wp-element-caption">AI 생성 이미지</figcaption>', "")
            wp_cli("post", "update", pid, f"--post_content={content}")
            print(f"[보정] {pid} 이미지 캡션 제거")
        content, n = repair_lists(content)   # 11/6 이전 변환이 깨뜨린 '목록 안 목록' (#872)
        if n:
            wp_cli("post", "update", pid, f"--post_content={content}")
            bulk[pid]["content"] = content
            print(f"[보정] {pid} 깨진 목록 안 목록 {n}곳 되살림")
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
    meta = p.get("meta")
    thumb, card = (meta["_thumbnail_id"], meta["_firstlab_card"]) if meta else (_meta(pid, "_thumbnail_id"), _meta(pid, "_firstlab_card"))
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
