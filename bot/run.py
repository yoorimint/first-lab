"""봇 실행 진입점. systemd 타이머가 1분마다 실행한다.

  - (키워드 수집은 별도 작업 firstlab-discover: 매일 + 배포 직후)
  - 하루 1회: 상위 후보 사전 검토 (안전 검사 + 경쟁도)
  - 관리자가 '지금 글 1개 만들기'를 눌렀으면 → 1개 생성
  - 자동 발행이 켜져 있고, 오늘의 랜덤 발행 시각이 지난 슬롯이 있으면 → 1개 생성
  - 한 번 실행에 글은 최대 1개 (몰아서 발행 방지)

사용: python3 -m bot.run            (평소: 할 일이 있을 때만 실행)
      python3 -m bot.run --now      (점검용: 즉시 1개 생성)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import random
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import factcheck, polish, related
from .discover import discover
from .keyword_db import connect, set_status, summary
from .llm import LLMBusy, make_llm
from .publisher import publish
from .select_keyword import pick_keyword, ranked_candidates, review_top
from .settings import BotSettings, load_settings, wp_cli
from .state import add_log, load_state, pop_excluded, pop_manual_request, save_state
from .writer import write_article

KST = ZoneInfo("Asia/Seoul")
LOCK_FILE = "/run/firstlab-bot.lock"
MAX_LATE_MINUTES = 180  # 서버 중단 등으로 이보다 늦어진 슬롯은 건너뜀 (몰아서 발행 방지)
MIN_POOL = 30           # 후보가 이보다 적으면 글 쓰기 전에 창고를 조금 확장
RETRY_REVIEW_MIN = 20   # AI 혼잡 시 사전 검토 재시도 간격(분)
RETRY_MANUAL_MIN = 5    # AI 혼잡 시 수동 요청 재시도 간격(분)
MANUAL_RETRIES = 12     # 수동 요청 최대 재시도 횟수
RETRY_AUTO_MIN = 10     # AI 혼잡 시 자동 발행 재시도 간격(분)


def plan_for_today(settings: BotSettings, today: str) -> list[str]:
    """오늘의 발행 시각 = 설정 시각 ± 랜덤(분). 날짜로 시드를 고정해 하루 동안 같은 값."""
    plan = []
    for i, hhmm in enumerate(settings.publish_times):
        base = datetime.strptime(f"{today} {hhmm}", "%Y-%m-%d %H:%M")
        offset = random.Random(f"{today}-{i}-{hhmm}").randint(-settings.jitter_minutes, settings.jitter_minutes)
        day_start = base.replace(hour=0, minute=0)
        t = min(max(base + timedelta(minutes=offset), day_start), day_start.replace(hour=23, minute=59))
        plan.append(t.strftime("%H:%M"))  # 날짜를 넘어가지 않게 00:00~23:59 로 제한
    return plan


def due_slot(state: dict, settings: BotSettings, now: datetime) -> int | None:
    """실행할 차례인 슬롯 번호 (없으면 None). 오늘 계획을 상태에 기록한다."""
    today = now.strftime("%Y-%m-%d")
    state["plan"] = {today: plan_for_today(settings, today)}            # 지난 날짜는 정리
    # 완료 기록은 '예정 시각(HH:MM)'으로 저장 (예전 버전의 회차 번호 기록은 무시)
    state["done"] = {today: [d for d in state["done"].get(today, []) if isinstance(d, str)]}
    for i, hhmm in enumerate(state["plan"][today]):
        if hhmm in state["done"][today] or now.strftime("%H:%M") < hhmm:
            continue
        due = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]), second=0, microsecond=0)
        if now - due > timedelta(minutes=MAX_LATE_MINUTES):
            state["done"][today].append(hhmm)  # 너무 늦음 → 건너뜀
            continue
        return i
    return None


def create_one(settings: BotSettings) -> str:
    """키워드 선정 → 글 작성 → 워드프레스 저장. 결과 메시지 반환."""
    llm = make_llm(settings.provider, settings.api_key, settings.model)
    with connect() as conn:
        if len(ranked_candidates(conn, settings)) < MIN_POOL:
            discover(5, settings.hint_keywords)
        light = make_llm(settings.provider, settings.api_key, settings.model, light=True)  # 판정·검증용 (저렴)
        keyword = pick_keyword(conn, settings, light)
        if not keyword:
            raise RuntimeError("안전 검사를 통과한 후보가 없습니다. 새벽 키워드 수집 후 다시 시도됩니다")
        cands = related.candidates(keyword)
        article = write_article(llm, keyword, settings.target_length, settings.extra_prompt,
                                persona=settings.persona, links=related.prompt_section(cands))
        allowed_links = {c["url"] for c in cands}
        status, check_note = settings.post_status, ""
        if settings.fact_check:
            try:
                # 검수는 Haiku 로 충분 (내부 블라인드 시험: Sonnet 검수와 품질 차이 없음, 비용은 약 1/20)
                fc = factcheck.verify(light, keyword, article.content_html)
                article.content_html, check_note = fc.content_html, ", " + fc.summary
                if fc.needs_review:
                    status = "draft"  # 확인 못 한 주장이 많으면 자동 발행 대신 초안
            except Exception as e:  # noqa: BLE001 - 검증 실패 시 원문 유지 + 초안
                status, check_note = "draft", f", 사실검증 실패({str(e)[:60]}) → 검토 필요"
        try:  # 마지막 교정: 어색한 문장·불일치·반복만 부분 수정 (실패해도 원문으로 진행)
            article.content_html, polished = polish.polish(llm, article.content_html)
            check_note += f", 교정 {polished}곳"
        except Exception as e:  # noqa: BLE001
            check_note += f", 교정 건너뜀({str(e)[:40]})"
        article.content_html, link_count = related.clean_links(article.content_html, allowed_links)
        post_id, link, image_note = publish(article, status, slug=keyword,
                                            cloudflare=(settings.cf_account_id, settings.cf_api_token),
                                            image_count=settings.image_count)
        set_status(conn, keyword, "used", f"post {post_id}")
    kind = "발행" if status == "publish" else "초안 저장"
    cost = getattr(llm, "cost_usd", 0.0) + getattr(light, "cost_usd", 0.0)
    cost_note = f", AI 비용 약 ${cost:.2f}(≈{cost * 1400:,.0f}원)" if cost else ""
    return (f"[{kind}] '{article.title}' (키워드: {keyword}, 모델: {llm.model}, {image_note}, "
            f"내부 링크 {link_count}개{check_note}{cost_note}) {link}")


CACHE_DIR = "/var/cache/nginx/first-lab"


def purge_cache_if_changed(state: dict) -> None:
    """가장 최근 발행·수정 시각이 바뀌었으면 nginx 페이지 캐시 삭제."""
    try:
        latest = wp_cli("post", "list", "--post_type=post,page", "--post_status=publish", "--orderby=modified",
                        "--order=DESC", "--posts_per_page=1", "--field=post_modified").strip()
    except subprocess.CalledProcessError:
        return
    if latest and latest != state.get("last_modified"):
        _sync_recent_cards(state.get("last_modified", ""))
        clear_cache()
        state["last_modified"] = latest


def _sync_recent_cards(since: str) -> None:
    """마지막 확인 이후 수정된 글의 제목 카드 갱신 (제목을 고치면 대표 이미지도 바뀌게)."""
    import json

    from .fix_posts import sync_card

    try:
        posts = json.loads(wp_cli("post", "list", "--post_type=post", "--post_status=any", "--orderby=modified",
                                  "--order=DESC", "--posts_per_page=5", "--fields=ID,post_title,post_name,post_modified",
                                  "--format=json") or "[]")
        for p in posts:
            if p["post_modified"] > since and p["post_title"]:
                sync_card(p)
    except Exception as e:  # noqa: BLE001 - 카드 갱신 실패가 봇을 막지 않게
        print(f"[카드] 갱신 실패: {e}", flush=True)


def clear_cache() -> None:
    """캐시 '내용'만 삭제. 폴더 자체를 지우면 nginx 가 캐시를 못 써서 빈 응답이 나감 (실제 장애 원인)."""
    import os
    import shutil

    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)
        shutil.chown(CACHE_DIR, "www-data", "www-data")
        return
    for name in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, name)
        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)


def refresh_dashboard(state: dict, settings: BotSettings) -> None:
    """설정 페이지에 보여줄 창고 현황과 다음 후보 (안전 검사 전 포함)."""
    with connect() as conn:
        state["keywords"] = summary(conn)
        state["candidates"] = ranked_candidates(conn, settings, limit=20)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="즉시 1개 생성")
    args = parser.parse_args()

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 이전 실행이 아직 작업 중이면 건너뜀
    except BlockingIOError:
        print("[run] 이전 실행이 아직 진행 중 → 건너뜀", flush=True)
        return

    settings = load_settings()
    state = load_state()
    before = json.dumps(state, sort_keys=True, ensure_ascii=False)
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    # 글이 발행·수정되면 페이지 캐시 비우기 (앱·관리자 화면에서 발행해도 1분 안에 반영)
    purge_cache_if_changed(state)

    # 관리자 '제외' 반영
    excluded = pop_excluded()
    if excluded:
        with connect() as conn:
            for kw in excluded:
                set_status(conn, kw, "excluded", "관리자 제외")

    # 하루 1회 상위 후보 사전 검토 (안전 검사 + 구글 상위 사이트 경쟁도) → 설정 페이지에 근거 표시
    stamp = now.strftime("%Y-%m-%d %H:%M")
    if state.get("review_date") != today and settings.api_key and stamp >= state.get("review_retry_at", ""):
        try:
            with connect() as conn:
                llm = make_llm(settings.provider, settings.api_key, settings.model, light=True)
                n = review_top(conn, settings, llm)
            add_log(state, True, f"후보 검토: 상위 후보 안전 검사 + 경쟁도 {n}건 조회")
            state["review_date"] = today
        except LLMBusy as e:  # 혼잡 → 20분 뒤 다시 (오늘 안에)
            state["review_retry_at"] = (now + timedelta(minutes=RETRY_REVIEW_MIN)).strftime("%Y-%m-%d %H:%M")
            add_log(state, False, f"후보 검토 보류: {e} → {state['review_retry_at'][11:]}에 다시 시도")
        except Exception as e:  # noqa: BLE001
            add_log(state, False, f"후보 검토 실패: {e}")
            state["review_date"] = today
        state["dashboard_at"] = ""  # 현황 즉시 갱신

    # 글 생성 (수동 요청 또는 자동 발행 시각)
    reason, slot = None, None
    if pop_manual_request():
        state["manual_attempts"], state["manual_next"] = 0, ""
        reason = "수동"
    elif args.now or (state.get("manual_next") and stamp >= state["manual_next"]):
        reason = "수동(재시도)" if not args.now else "수동"
    slot_candidate = due_slot(state, settings, now)
    if reason is None and settings.enabled and slot_candidate is not None and stamp >= state.get("auto_next", ""):
        reason, slot = f"자동 {slot_candidate + 1}회차", slot_candidate

    if reason:
        try:
            msg = create_one(settings)
            add_log(state, True, f"{reason}: {msg}")
            state["manual_next"] = ""
            print(msg)
        except LLMBusy as e:  # 혼잡·한도 → 수동 요청은 5분 뒤 자동 재시도 (최대 12번 = 1시간)
            attempts = state.get("manual_attempts", 0) + 1
            if reason.startswith("수동") and attempts <= MANUAL_RETRIES:
                state["manual_attempts"] = attempts
                state["manual_next"] = (now + timedelta(minutes=RETRY_MANUAL_MIN)).strftime("%Y-%m-%d %H:%M")
                add_log(state, False, f"{reason} 보류: {e} → {state['manual_next'][11:]}에 자동 재시도 ({attempts}/{MANUAL_RETRIES})")
            elif slot is not None:  # 자동 발행: 10분 뒤 같은 회차 재시도 (예정 시각 3시간 지나면 포기)
                state["auto_next"] = (now + timedelta(minutes=RETRY_AUTO_MIN)).strftime("%Y-%m-%d %H:%M")
                add_log(state, False, f"{reason} 보류: {e} → {state['auto_next'][11:]}에 재시도")
                slot = None  # 완료 처리하지 않음
            else:
                state["manual_next"] = ""
                add_log(state, False, f"{reason} 실패: {e}")
        except Exception as e:  # noqa: BLE001 - 어떤 오류든 기록하고 다음 실행에 영향 없게
            state["manual_next"] = ""
            detail = e.stderr if isinstance(e, subprocess.CalledProcessError) and e.stderr else e
            add_log(state, False, f"{reason} 실패: {detail}")
            print(f"실패: {detail}", file=sys.stderr)
        if slot is not None:  # 실패해도 같은 슬롯을 1분마다 재시도하지 않도록 완료 처리
            state["done"][today].append(state["plan"][today][slot])

    if reason or excluded or state.get("dashboard_at", "")[:13] != now.strftime("%Y-%m-%d %H"):
        refresh_dashboard(state, settings)  # 변화가 있거나 1시간마다
        state["dashboard_at"] = now.strftime("%Y-%m-%d %H:%M")

    if json.dumps(state, sort_keys=True, ensure_ascii=False) != before:
        save_state(state)


if __name__ == "__main__":
    main()
