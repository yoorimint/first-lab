"""저장소에 넣은 완성 글(wordpress/queue/*.json)을 사이트 발행 파이프라인으로 올린다 (배포 때 실행).

봇의 AI 작성 단계만 건너뛰고, 나머지는 봇과 똑같이 처리한다:
제목·요약문 숫자 점검 → 링크 정리 → AI 이미지·대표 카드·카테고리·작성자 → 키워드 '사용됨' 기록.
파일 형식: 글쓰기 AI 출력과 같은 JSON + "keyword"(필수), "status"(draft|publish|auto, 기본 draft).
글 갱신 파일: {"type": "refresh", "post_id", "base_modified", "content_html", "changes"} → refresh.apply_direct
같은 파일은 한 번만 올린다 (서버의 완료 표시 파일로 확인 → 파일 내용을 바꾸면 새 글로 다시 올라감).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import refresh, related
from .keyword_db import connect, norm, set_status
from .publisher import STATUS_LABEL, publish
from .settings import load_settings
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
    # status: draft | publish | auto(설정 페이지의 발행 방식을 따름)
    status = data.get("status", "draft")
    if status == "auto":
        status = settings.post_status
    if status not in STATUS_LABEL:
        status = "draft"
    article = parse_article(json.dumps(data, ensure_ascii=False))
    notes = []
    aligned = align_with_body(article)
    if aligned:
        notes.append(f"본문과 숫자 불일치 → {'·'.join(aligned)} 보정")
        if "제목(확인 필요)" in aligned:
            status = "draft"
    article.content_html, _ = related.clean_links(article.content_html, allowed_links)
    post_id, link, image_note = publish(article, status, slug=keyword,
                                        cloudflare=(settings.cf_account_id, settings.cf_api_token),
                                        image_count=settings.image_count, schedule_times=settings.schedule_times)
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM keywords WHERE norm=?", (norm(keyword),)).fetchone()
        if exists:
            set_status(conn, keyword, "used", f"post {post_id} (직접 작성)")
            conn.commit()
    kind = STATUS_LABEL[status]
    extra = f", {', '.join(notes)}" if notes else ""
    return f"[{kind}] '{article.title}' (키워드: {keyword}, 직접 작성, {image_note}{extra}) {link}"


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
        # 로그 머리말 '직접 작성 글:' 은 봇이 예비 발행을 건너뛰는 기준이라 새 글에만 쓴다 (run.direct_post_today)
        label = "직접 작성 글"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("type") == "refresh":
                label = "글 갱신(직접)"
                msg = refresh.apply_direct(data, state)
            else:
                msg = publish_file(path, settings, allowed_links)
            _marker(path).touch()
            msg = f"{label}: {msg}"
            add_log(state, True, msg)
        except Exception as e:  # noqa: BLE001 - 한 파일 실패가 배포를 막지 않게 (다음 배포 때 다시 시도)
            msg = f"{label} 실패 ({path.name}): {e}"
            add_log(state, False, msg)
        print(msg, flush=True)
    save_state(state)


if __name__ == "__main__":
    main()
