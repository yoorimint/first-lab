"""공개 직전 확인 (코드만, Claude 사용량 없음): 초안을 쓴 뒤 공개까지 기간에 근거가 바뀌었는지 본다.

왜: 공개 대기 초안이 많으면 쓴 지 몇 주 뒤에 공개된다. 그사이 법 개정이 시행되거나 기관 안내 금액이 바뀌면
글이 틀린 채 나간다 (11/5 운영자 지적). 공개 때 Claude 가 다시 검수하지 않고(운영자 결정), 코드로 두 가지만 확인한다.
  1. 글이 근거로 단 법령 조문이 '확인한 날' 뒤에 개정·시행됐는지 (조문의 <개정 …> 날짜, 부칙의 '○조 개정규정은 … 시행')
  2. 쓸 때 공식 페이지에서 확인된 숫자가 지금도 그 페이지에 있는지 (bot/source_check.py 의 found)
바뀐 게 있으면 공개하지 않고 보류(검수 보류 초안) → 글쓰기 세션이 보류 수정 절차로 다시 확인해 고친다.
확인 자체가 실패(접속 오류 등)하면 막지 않는다 — 바뀌었다는 증거가 있을 때만 보류.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse
from pathlib import Path

from . import changed, source_check
from .review_gate import _ok
from .settings import wp_cli

VERIFIED_META = "_firstlab_verified_at"  # 마지막으로 검수를 통과한 날 (초안 저장·보류 수정 통과 때 기록)
QUEUE_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "queue"


def changed_since(text: str, article: str, since: dt.date, today: dt.date) -> list[str]:
    """조문 본문·부칙에서 since 뒤 ~ today 사이에 개정·시행된 날짜."""
    body = source_check.article_text(text, article)
    dates = [dt.date(int(y), int(m), int(d)) for y, m, d in re.findall(r"(20\d\d)\.\s*(\d{1,2})\.\s*(\d{1,2})\.", body)]
    for y, m, d in re.findall(re.escape(article) + r"[^.]{0,80}?개정규정은\s*(20\d\d)년\s*(\d{1,2})월\s*(\d{1,2})일", text):
        dates.append(dt.date(int(y), int(m), int(d)))
    return sorted({f"{x:%Y.%m.%d}" for x in dates if since < x <= today})


def law_effective_since(text: str, since: dt.date, today: dt.date) -> list[str]:
    """법령 전체 부칙의 시행일 중 since 뒤 ~ today (조문 번호를 모르는 예전 초안용 — 넓게 잡아 다시 확인하게 함)."""
    dates = [dt.date(int(y), int(m), int(d)) for y, m, d in
             re.findall(r"(20\d\d)년\s*(\d{1,2})월\s*(\d{1,2})일부터\s*시행", text)]
    return sorted({f"{x:%Y.%m.%d}" for x in dates if since < x <= today})


def _review_from_queue(keyword: str) -> list[dict]:
    """예전 초안(근거 대조에 laws 가 없는 것)은 저장소 큐 파일의 검수 기록에서 근거를 찾는다."""
    for path in QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(data.get("keyword", "")).strip() == keyword and data.get("review"):
            return data["review"]  # 글 파일에 없으면 근거 기록 파일(type evidence)의 것
    return []


def problems(record: dict, review: list[dict], since: dt.date, today: dt.date, fetcher=source_check.fetch) -> list[str]:
    out, cache = [], {}

    def page(url: str) -> str | None:
        if url not in cache:
            cache[url] = fetcher(url)
        return cache[url]

    laws = record.get("laws") or sorted({(source_check.law_page(str(u)), art) for item in review if _ok(item)
                                         for u in item.get("urls", []) for art in [source_check.claim_article(item, str(u))] if art})
    for base, art in laws:
        found = changed_since(page(base) or "", art, since, today)
        if found:
            out.append(f"{urllib.parse.unquote(base).rsplit('/', 1)[-1]} {art} {', '.join(found)} 개정·시행")
    # 조문 번호 없이 법령만 근거로 단 주장 (11/5 조문 번호 규칙 전에 쓴 초안): 그 법령이 검수 뒤 시행됐는지
    covered = {base for base, _ in laws}
    whole = sorted({source_check.law_page(str(u)) for item in review if _ok(item) for u in item.get("urls", [])
                    if "law.go.kr" in str(u) and not source_check.claim_article(item, str(u))} - covered)
    for base in whole:
        found = law_effective_since(page(base) or "", since, today)
        if found:
            out.append(f"{urllib.parse.unquote(base).rsplit('/', 1)[-1][:30]} {', '.join(found)} 시행 (조문 확인 필요)")
    for item in record.get("found", []):
        texts = [t for t in (page(u) for u in item.get("urls", [])) if t]
        if not texts:
            continue  # 지금 못 열면 판단하지 않음
        gone = [n for n in item.get("numbers", []) if not any(source_check.has_number(t, n) for t in texts)]
        if gone:
            out.append(f"'{item['claim'][:30]}' 숫자 {', '.join(gone)} 가 공식 페이지에서 사라짐")
    return out


def check_post(post_id: int, today: dt.date | None = None) -> list[str]:
    """공개 전에 다시 확인할 이유 (빈 목록이면 공개해도 됨)."""
    today = today or dt.date.today()
    get = lambda field: wp_cli("post", "get", str(post_id), f"--field={field}").strip()  # noqa: E731
    try:
        verified = wp_cli("post", "meta", "get", str(post_id), VERIFIED_META).strip()
    except Exception:  # noqa: BLE001
        verified = ""
    since = dt.date.fromisoformat((verified or get("post_date"))[:10])
    try:
        record = json.loads(wp_cli("post", "meta", "get", str(post_id), source_check.META) or "{}")
    except Exception:  # noqa: BLE001
        record = {}
    review = [] if record.get("laws") else _review_from_queue(urllib.parse.unquote(get("post_name")))
    out = problems(record, review, since, today)
    if not record.get("found") and not record.get("laws") and not review:
        # 근거 기록이 하나도 없는 글은 바뀌어도 알 길이 없음 → 공개 전에 근거부터 (11/5 운영자 '예전 봇 글도 근거 기록 만들어라')
        out.append("근거 기록 없음 (어느 공식 출처로 확인했는지 남기기)")
    try:  # 쓴 뒤에 근거(법령·금리·혜택 내용·계산기 값)가 바뀌었다는 표시 (bot/changed.py)
        mark = wp_cli("post", "meta", "get", str(post_id), changed.META).strip()
    except Exception:  # noqa: BLE001
        mark = ""
    if changed.pending(mark, f"{since:%Y-%m-%d}") and mark[:10] <= f"{today:%Y-%m-%d}":
        out.append(f"쓴 뒤 근거 변경: {mark[:150]}")
    return out


def mark_verified(post_id: int | str, when: dt.date | None = None) -> None:
    """검수 통과 날짜 기록. 실패해도 글 저장·공개는 계속 (없으면 글 작성일을 기준으로 씀)."""
    try:
        wp_cli("post", "meta", "update", str(post_id), VERIFIED_META, f"{(when or dt.date.today()):%Y-%m-%d}")
    except Exception:  # noqa: BLE001
        pass
