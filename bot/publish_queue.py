"""저장소에 넣은 완성 글(wordpress/queue/*.json)을 사이트 발행 파이프라인으로 올린다 (배포 때 실행).

봇의 AI 작성 단계만 건너뛰고, 나머지는 봇과 똑같이 처리한다:
제목·요약문 숫자 점검 → 링크 정리 → AI 이미지·대표 카드·카테고리·작성자 → 키워드 '사용됨' 기록.
파일 형식: 글쓰기 AI 출력과 같은 JSON + "keyword"(필수), "status"(draft|publish|schedule|auto, 기본 draft),
  "sources"(선택, [{"name", "url"}] → 글 끝 '공식 안내 바로가기', sources.py),
  "review"(독립 검수 기록 → review_gate.py. 통과 못 하면 공개하지 않고 초안 보류).
글 갱신 파일: {"type": "refresh", "post_id", "base_modified", "content_html", "changes"} → refresh.apply_direct
보류 초안 수정 파일: {"type": "fix_hold", ...} → hold.apply_fix (다시 관문 검사 후 대기열)
후보 제외 파일: {"type": "skip", "keywords": [{"keyword", "why"}], "law_changes": [key], "new_benefits": [key]}
  → 글쓰기 세션이 부적합하다고 본 후보·다룰 내용 없는 제도/혜택 글감을 다시 안 뜨게 함
사진 검사 파일: {"type": "image_check", "post_id", "replace", "remove"} → image_check.apply_check (엉터리 글자 사진 교체)
같은 파일은 한 번만 올린다 (서버의 완료 표시 파일로 확인 → 파일 내용을 바꾸면 새 글로 다시 올라감).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import freshness, hold, image_check, refresh, related, review_gate, source_check, sources
from .keyword_db import connect, norm, set_status
from .publisher import STATUS_LABEL, existing_post_id, publish, similar_post
from .settings import load_settings, wp_cli
from .state import add_log, load_state, save_state
from .writer import align_with_body, parse_article

QUEUE_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "queue"
DONE_DIR = Path("/var/lib/first-lab/bot/queue-done")


def _marker(path: Path) -> Path:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return DONE_DIR / f"{path.stem}-{digest}"


def pending() -> list[Path]:
    return [p for p in sorted(QUEUE_DIR.glob("*.json")) if not _marker(p).exists()]


def publish_file(path: Path, settings, allowed_links: set[str]) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    keyword = str(data["keyword"]).strip()
    # 같은 키워드 글이 이미 있으면 새로 만들지 않음 (11/4 연속 작성 동시 실행으로 실업급여 글이 두 편 생긴 일)
    duplicate = existing_post_id(keyword)
    if duplicate:
        return f"[건너뜀] 중복 키워드 '{keyword}' — 글 {duplicate} 이 이미 있음"
    similar, slug = similar_post(keyword)
    if similar:  # 글자 몇 개만 다른 같은 주제 (11/5 '대부업체대출조회' ↔ '대부업대출조회')
        return f"[건너뜀] 비슷한 주제 '{keyword}' — 글 {similar}({slug}) 이 이미 있음"
    # status: draft | publish | auto(설정 페이지의 발행 방식을 따름)
    status = data.get("status", "draft")
    if status == "auto":
        status = settings.post_status
    if status not in STATUS_LABEL:
        status = "draft"
    article = parse_article(json.dumps(data, ensure_ascii=False))
    notes = []
    # 초안 저장 관문: 검수를 통과 못 한 글은 공개(바로·예약)하지 않고 초안으로만 둔다
    failed = review_gate.problems(data.get("review"), article.title, article.excerpt, article.content_html)
    if failed:
        notes.append(f"검수 미통과 → 초안 보류: {'; '.join(failed)[:200]}")
        status = "draft"
    aligned = align_with_body(article)
    if aligned:
        notes.append(f"본문과 숫자 불일치 → {'·'.join(aligned)} 보정")
        if "제목(확인 필요)" in aligned:
            status = "draft"
    article.content_html, _ = related.clean_links(article.content_html, allowed_links)
    # 글 끝 '공식 안내 바로가기' (세션이 확인한 공식 페이지 중 실제로 열리는 것만)
    official, dropped = sources.section(data.get("sources", []))
    article.content_html += official
    if dropped:
        notes.append(f"공식 링크 제외 {len(dropped)}개: {'; '.join(dropped)[:150]}")
    post_id, link, image_note = publish(article, status, slug=keyword,
                                        cloudflare=(settings.cf_account_id, settings.cf_api_token))
    if failed:  # 설정 페이지 '검수 보류 초안' 목록에 이유와 함께 보이게
        wp_cli("post", "meta", "update", str(post_id), review_gate.HOLD_META, "; ".join(failed)[:300])
    if not failed:
        freshness.mark_verified(post_id)  # 공개 직전 확인(bot/freshness.py)의 기준일
    # 근거 링크를 열어 주장 숫자 대조 — 지금은 기록만 (bot/source_check.py)
    notes.append(source_check.record(post_id, data.get("review"),
                                     lambda pid, key, val: wp_cli("post", "meta", "update", pid, key, val)))
    if data.get("new_benefit"):  # '새로 생긴 혜택' 글 (/next 의 new_benefits 항목 key) → 다시 글감으로 안 줌
        state = load_state()
        done = state.get("new_benefits_done") or []
        state["new_benefits_done"] = (done + [str(data["new_benefit"])])[-300:]
        save_state(state)
    if data.get("law_change"):  # '바뀌는 제도' 글 (/next 의 law_changes 항목 key) → 같은 변경은 다시 글감으로 안 줌
        state = load_state()
        done = state.get("law_changes_done") or []
        state["law_changes_done"] = (done + [str(data["law_change"])])[-200:]
        save_state(state)
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM keywords WHERE norm=?", (norm(keyword),)).fetchone()
        if exists:
            set_status(conn, keyword, "used", f"post {post_id} (직접 작성)")
            conn.commit()
    kind = STATUS_LABEL[status]
    extra = f", {', '.join(notes)}" if notes else ""
    return f"[{kind}] '{article.title}' (키워드: {keyword}, 직접 작성, {image_note}{extra}) {link}"


def skip_keywords(data: dict) -> str:
    """글쓰기 세션이 건너뛴 후보를 '부적합'으로 기록 (월정액 방식의 후보 안전 검사 대신)."""
    items = [i for i in data.get("keywords", []) if str(i.get("keyword", "")).strip()]
    with connect() as conn:
        for item in items:
            set_status(conn, str(item["keyword"]).strip(), "rejected", f"글쓰기 세션 제외: {str(item.get('why', ''))[:80]}")
        conn.commit()
    # 다룰 내용이 없는 '바뀌는 제도'·'새 혜택' 글감도 다시 안 주게 (안 그러면 다음 실행에 같은 첫 항목이 또 나옴)
    skipped = {"law_changes_done": [str(k) for k in data.get("law_changes", [])],
               "new_benefits_done": [str(k) for k in data.get("new_benefits", [])]}
    if any(skipped.values()):
        state = load_state()
        for field, keys in skipped.items():
            state[field] = ((state.get(field) or []) + keys)[-300:]
        save_state(state)
    extra = sum(len(v) for v in skipped.values())
    return (f"{len(items)}개 ({', '.join(str(i['keyword']) for i in items[:5])}{' 등' if len(items) > 5 else ''})"
            + (f", 건너뛴 제도·혜택 글감 {extra}개" if extra else ""))


def main() -> None:
    files = pending()
    if not files:
        print("올릴 글 없음")
        return
    settings = load_settings()
    state = load_state()
    allowed_links = related.allowed_urls()  # 우리 발행 글·계산기 페이지 링크만 남김
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    for path in files:
        # 로그 머리말 '직접 작성 글:' 은 연속 작성이 '한 편 완료'를 아는 기준이라 새 글에만 쓴다 (run.post_arrived_since)
        label = "직접 작성 글"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("type") == "refresh":
                label = "글 갱신(직접)"
                msg = refresh.apply_direct(data, state)
            elif data.get("type") == "fix_hold":
                label = "보류 초안 수정"
                msg = hold.apply_fix(data)
            elif data.get("type") == "skip":
                label = "후보 제외(직접)"
                msg = skip_keywords(data)
            elif data.get("type") == "image_check":
                label = "사진 검사"
                msg = image_check.apply_check(data)
            else:
                msg = publish_file(path, settings, allowed_links)
            _marker(path).touch()
            msg = f"{label}: {msg}"
            add_log(state, True, msg)
        except Exception as e:  # noqa: BLE001 - 한 파일 실패가 배포를 막지 않게 (다음 배포 때 다시 시도)
            msg = f"{label} 실패 ({path.name}): {e}"
            add_log(state, False, msg)
        print(msg, flush=True)
    # 새 글(키워드 사용됨)·후보 제외가 반영됐으니 1분 안에 후보 목록을 새로 만들게 함 (읽기 API 가 옛 후보를 주지 않도록)
    state["dashboard_at"] = ""
    save_state(state)


if __name__ == "__main__":
    main()
