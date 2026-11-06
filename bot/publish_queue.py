"""저장소에 넣은 완성 글(wordpress/queue/*.json)을 사이트 발행 파이프라인으로 올린다 (배포 때 실행).

봇의 AI 작성 단계만 건너뛰고, 나머지는 봇과 똑같이 처리한다:
제목·요약문 숫자 점검 → 링크 정리 → AI 이미지·대표 카드·카테고리·작성자 → 키워드 '사용됨' 기록.
파일 형식: 글쓰기 AI 출력과 같은 JSON + "keyword"(필수), "status"(draft|publish|schedule|auto, 기본 draft),
  "sources"(선택, [{"name", "url"}] → 글 끝 '공식 안내 바로가기', sources.py),
  "review"(독립 검수 기록 → review_gate.py. 통과 못 하면 공개하지 않고 초안 보류).
글 갱신 파일: {"type": "refresh", "post_id", "base_modified", "content_html", "changes"} → refresh.apply_direct
보류 초안 수정 파일: {"type": "fix_hold", ...} → hold.apply_fix (다시 관문 검사 후 대기열)
후보 제외 파일: {"type": "skip", "keywords": [{"keyword", "why", "brand"(브랜드·회사 상품이면 그 이름)}], "law_changes": [key], "new_benefits": [key]}
  → 글쓰기 세션이 부적합하다고 본 후보·다룰 내용 없는 제도/혜택 글감을 다시 안 뜨게 함
사진 검사 파일: {"type": "image_check", "post_id", "replace", "remove"} → image_check.apply_check (엉터리 글자 사진 교체)
대기 글 검수 완료 파일: {"type": "review_done", "post_id", "title", "patches"} → 기록만 (연속 작성이 '한 번 끝남'으로 셈)
실행 끝 파일: {"type": "run_done", "summary"} → 기록만 (글쓰기 작업이 실행마다 마지막에 올림 — 연속 작성이 '한 번 끝남'으로 셈)
근거 기록 파일: {"type": "evidence", "post_id", "keyword", "review": [글쓰기 검수와 같은 형식]} → 근거 기록이 없던 예전 봇 글에
  근거 대조 기록을 만듦 (11/5 운영자 '예전 봇 글도 근거 기록 만들어라') → 법령 감시·공개 직전 확인·공개 글 다시 보기가 이 글도 봄
같은 파일은 한 번만 올린다 (서버의 완료 표시 파일로 확인 → 파일 내용을 바꾸면 새 글로 다시 올라감).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
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


MAX_FILE_FAILS = 3


def _bump_file_fail(path: Path) -> int:
    """같은 내용의 파일이 실패한 횟수 (완료 표시 파일 옆 .fail)."""
    fail = _marker(path).with_suffix(".fail")
    try:
        n = int(fail.read_text().strip() or 0) + 1
    except (OSError, ValueError):
        n = 1
    try:
        DONE_DIR.mkdir(parents=True, exist_ok=True)
        fail.write_text(str(n))
    except OSError:
        pass
    return n


def pending() -> list[Path]:
    return [p for p in sorted(QUEUE_DIR.glob("*.json")) if not _marker(p).exists()]


def publish_file(path: Path, settings, allowed_links: set[str]) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    keyword = str(data["keyword"]).strip()
    # 같은 키워드 글이 이미 있으면 새로 만들지 않음 (11/4 연속 작성 동시 실행으로 실업급여 글이 두 편 생긴 일)
    duplicate = existing_post_id(keyword)
    similar, slug = ("", "") if duplicate else similar_post(keyword)  # 글자 몇 개만 다른 같은 주제 (11/5 '대부업체대출조회' ↔ '대부업대출조회')
    if duplicate or similar:
        try:  # 건너뛰어도 그 제도·혜택 글감은 끝난 것으로·키워드는 '사용됨' (11/5 검토: 매 실행 같은 글감을 다시 씀)
            _record_topic_done(data)
            with connect() as conn:
                if conn.execute("SELECT 1 FROM keywords WHERE norm=?", (norm(keyword),)).fetchone():
                    set_status(conn, keyword, "used", f"이미 있는 글 {duplicate or similar}")
                    conn.commit()
        except Exception:  # noqa: BLE001 - 기록 실패가 건너뛰기 판단을 막지 않게 (검사 환경엔 키워드 창고가 없음)
            pass
        return f"[건너뜀] {'중복 키워드' if duplicate else '비슷한 주제'} '{keyword}' — 글 {duplicate or similar}{f'({slug})' if similar else ''} 이 이미 있음"
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
    elif status == "draft" and (data.get("status") == "draft" or (aligned and "제목(확인 필요)" in aligned)):
        # 관문은 통과했는데 초안으로 남는 글(글쓴이가 draft 로 올림·제목 숫자 확인 필요)도 보류 목록으로 —
        # 안 그러면 대기열에도 보류에도 없어 12/5 뒤 영영 방치됨 (11/5 검토)
        why = "제목 숫자가 본문에 없음 (제목·본문 맞추기)" if aligned and "제목(확인 필요)" in aligned else "글쓴이가 초안으로 둠 (핵심 근거 보강 필요)"
        wp_cli("post", "meta", "update", str(post_id), review_gate.HOLD_META, why)
    if not failed:
        freshness.mark_verified(post_id)  # 공개 직전 확인(bot/freshness.py)의 기준일
    # 근거 링크를 열어 주장 숫자 대조 — 지금은 기록만 (bot/source_check.py)
    notes.append(source_check.record(post_id, data.get("review"),
                                     lambda pid, key, val: wp_cli("post", "meta", "update", pid, key, val)))
    _record_topic_done(data)
    with connect() as conn:
        exists = conn.execute("SELECT total FROM keywords WHERE norm=?", (norm(keyword),)).fetchone()
        if exists:
            set_status(conn, keyword, "used", f"post {post_id} (직접 작성)")
            conn.commit()
        notes.append(golden_note(conn, keyword, exists[0] if exists else 0))
    kind = STATUS_LABEL[status]
    extra = f", {', '.join(notes)}" if notes else ""
    return f"[{kind}] '{article.title}' (키워드: {keyword}, 직접 작성, {image_note}{extra}) {link}"


def save_evidence(data: dict) -> str:
    """예전 글에 근거 대조 기록 (bot/source_check.record) + 확인한 날 (공개 직전 확인·다시 보기의 기준일)."""
    post_id = str(int(data["post_id"]))
    review = data.get("review") or []
    if not review:
        raise ValueError("review 가 비어 있음")
    note = source_check.record(post_id, review, lambda pid, key, val: wp_cli("post", "meta", "update", pid, key, val))
    freshness.mark_verified(post_id)
    return f"글 {post_id} ({data.get('keyword', '')}) 주장 {len(review)}개 — {note}"


def golden_note(conn, keyword: str, total: int) -> str:
    """기록에 남길 황금 키워드 정보 (11/5 운영자 '김해공항 주차장 황금 키워드인가?' — 글마다 바로 알 수 있게)."""
    try:
        from . import naver_hub
        conn.execute(naver_hub.SCHEMA)
        cur = conn.execute("SELECT * FROM naver_signals WHERE norm=?", (norm(keyword),))
        row = cur.fetchone()
        sig = dict(zip([c[0] for c in cur.description], row)) if row else None
        text = naver_hub.describe(sig, int(total or 0), keyword)
    except Exception as e:  # noqa: BLE001 - 기록용이라 실패해도 글은 올림
        return f"네이버 신호 못 읽음({type(e).__name__})"
    return f"네이버: {text}" if text else f"네이버 신호 없음 (검색량 {int(total or 0):,})"


def _record_topic_done(data: dict) -> None:
    """'새로 생긴 혜택'·'바뀌는 제도' 글감 key 를 끝난 것으로 (/next 에 다시 안 나옴)."""
    pairs = (("new_benefit", "new_benefits_done", 300), ("law_change", "law_changes_done", 200))
    if not any(data.get(k) for k, _, _ in pairs):
        return
    state = load_state()
    for key, field, cap in pairs:
        if data.get(key):
            state[field] = ((state.get(field) or []) + [str(data[key])])[-cap:]
    save_state(state)


def skip_keywords(data: dict) -> str:
    """글쓰기 세션이 건너뛴 후보를 '부적합'으로 기록 (월정액 방식의 후보 안전 검사 대신)."""
    items = [i for i in data.get("keywords", []) if str(i.get("keyword", "")).strip()]
    # 브랜드·회사 상품이라 건너뛴 것: 그 브랜드가 든 다른 후보도 한 번에 제외 (11/5 SBI저축은행신용대출 제외 뒤에도
    # SBI저축은행대환대출이 상위 후보로 남아 글쓰기 세션이 매번 다시 건너뜀)
    from .keyword_filter import learn_brands
    new_brands = learn_brands(i["brand"] for i in items if str(i.get("brand", "")).strip())
    with connect() as conn:
        for item in items:
            set_status(conn, str(item["keyword"]).strip(), "rejected", f"글쓰기 세션 제외: {str(item.get('why', ''))[:80]}")
        same_brand = 0
        for brand in new_brands:
            same_brand += conn.execute("UPDATE keywords SET status='rejected', reason=? WHERE status IN ('new','ok') AND norm LIKE ?",
                                       (f"브랜드 '{brand}' (글쓰기 세션이 제외한 브랜드)", f"%{brand}%")).rowcount
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
            + (f", 건너뛴 제도·혜택 글감 {extra}개" if extra else "")
            + (f", 새 브랜드 {', '.join(new_brands)} → 같은 브랜드 후보 {same_brand}개 제외" if new_brands else ""))


def run_marks() -> tuple[str, ...]:
    from .run import DONE_MARKS
    return DONE_MARKS


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
            elif data.get("type") == "review_done":  # 글쓰기 작업이 새 글 대신 대기 글을 외부 검수함 (bot/review_queue.py)
                label = "대기 글 검수(직접)"
                msg = f"글 {int(data['post_id'])} '{str(data.get('title', ''))[:40]}' 고친 곳 {int(data.get('patches', 0))}개"
                if int((state.get("review_target") or {}).get("post_id", 0) or 0) == int(data["post_id"]):
                    state["review_target"] = None  # 바로 다음 실행이 같은 글을 또 검수하지 않게 (11/5 #690 두 번 검수 — 대상은 1분 봇이 새로 고름)
            elif data.get("type") == "run_done":  # 보류 수정·계산기만 한 실행도 '끝남'으로 (bot/run.DONE_MARKS)
                label = "글쓰기 실행 끝(직접)"
                slot = f"[{int(data['slot'])}번] " if str(data.get("slot", "")).isdigit() else ""  # 동시 실행 칸 (bot/run.WRITERS)
                msg = slot + (str(data.get("summary", ""))[:200] or "-")
            elif data.get("type") == "evidence":
                label = "근거 기록"
                msg = save_evidence(data)
            elif data.get("type") == "image_check":
                label = "사진 검사"
                msg = image_check.apply_check(data)
            else:
                msg = publish_file(path, settings, allowed_links)
            _marker(path).touch()
            msg = f"{label}: {msg}"
            add_log(state, True, msg)
            if f"{label}:" in run_marks():  # '보류 수정' 오타로 보류 수정 실행이 /health 에 안 잡혔음 → 같은 목록을 씀
                state["last_done_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")  # /health (외부 알림)
        except Exception as e:  # noqa: BLE001 - 한 파일 실패가 배포를 막지 않게 (다음 배포 때 다시 시도)
            fails = _bump_file_fail(path)
            if fails >= MAX_FILE_FAILS:  # 다시 해도 안 되는 파일을 배포마다 영원히 돌리지 않게 (11/5 검토)
                _marker(path).touch()
                msg = f"{label} 포기 ({path.name}, {fails}번 실패): {e}"
            else:
                msg = f"{label} 실패 ({path.name}, {fails}/{MAX_FILE_FAILS}번째): {e}"
            add_log(state, False, msg)
        print(msg, flush=True)
    # 새 글(키워드 사용됨)·후보 제외가 반영됐으니 1분 안에 후보 목록을 새로 만들게 함 (읽기 API 가 옛 후보를 주지 않도록)
    state["dashboard_at"] = ""
    save_state(state)


if __name__ == "__main__":
    main()
