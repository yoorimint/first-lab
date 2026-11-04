"""봇 실행 진입점. systemd 타이머가 1분마다 실행한다.

  - (키워드 수집은 별도 작업 firstlab-discover: 매일 + 배포 직후)
  - 하루 1회: 상위 후보 사전 검토 (API 봇: 안전 검사 + 경쟁도 / 월정액: 경쟁도만)
  - 월정액: '직접 작성 시각'이 되면 Claude 예약 작업 실행 (bot/routine.py, API 요금 없음)
  - 관리자가 '지금 글 1개 만들기'를 눌렀으면 → 월정액: Claude 예약 작업 실행 / API 봇: 1개 생성
  - 자동 발행이 켜져 있고, 오늘의 랜덤 발행 시각이 지난 슬롯이 있으면 → 1개 생성 (월정액에선 예비)
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

from . import competition, factcheck, related, routine
from .discover import discover
from .keyword_db import connect, set_competition, set_status, summary
from .llm import LLMBusy, make_llm
from .publisher import STATUS_LABEL, publish
from .select_keyword import check_top_competition, pick_keyword, ranked_candidates, review_top
from .settings import BotSettings, load_settings, wp_cli
from .state import add_log, add_spend, load_state, pop_excluded, pop_manual_request, save_state, spent_today
from .writer import align_with_body, write_article

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


def _record_competition(conn, keyword: str, urls: list[str]) -> None:
    """글쓰기 때 한 웹 검색 결과로 이 키워드의 경쟁도를 기록 (무료). 같은 주제 키워드 추정에 쓰인다."""
    comp = competition.from_urls(urls)
    if comp is None:
        return
    set_competition(conn, keyword, comp.difficulty, f"글쓰기 검색: {comp.summary}: {', '.join(comp.domains)}")
    conn.commit()


def create_one(settings: BotSettings, used_llms: list | None = None) -> str:
    """키워드 선정 → 글 작성 → 워드프레스 저장. 결과 메시지 반환.
    used_llms: 만든 AI 객체를 담아 돌려줌 → 실패해도 호출한 쪽에서 쓴 비용을 셀 수 있게."""
    used_llms = used_llms if used_llms is not None else []
    llm = make_llm(settings.provider, settings.api_key, settings.model)
    used_llms.append(llm)
    with connect() as conn:
        if len(ranked_candidates(conn, settings)) < MIN_POOL:
            discover(5, settings.hint_keywords)
        light = make_llm(settings.provider, settings.api_key, settings.model, light=True)  # 판정·검증용 (저렴)
        used_llms.append(light)
        keyword = pick_keyword(conn, settings, light)
        if not keyword:
            raise RuntimeError("안전 검사를 통과한 후보가 없습니다. 새벽 키워드 수집 후 다시 시도됩니다")
        cands = related.candidates(keyword)
        article = write_article(llm, keyword, settings.target_length, settings.extra_prompt,
                                persona=settings.persona, links=related.prompt_section(cands))
        allowed_links = {c["url"] for c in cands}
        _record_competition(conn, keyword, getattr(llm, "search_urls", []))
        status, check_note = settings.post_status, ""
        if settings.fact_check:
            try:
                # 검수는 Haiku 로 충분 (내부 블라인드 시험: Sonnet 검수와 품질 차이 없음, 비용은 약 1/20)
                fc = factcheck.verify(light, keyword, article.content_html, article.title, article.excerpt)
                article.content_html, check_note = fc.content_html, ", " + fc.summary
                if fc.title and len(fc.title) <= 60:
                    article.title = fc.title
                if fc.excerpt:
                    article.excerpt = fc.excerpt[:200]
                if fc.needs_review:
                    status = "draft"  # 확인 못 한 주장이 많으면 자동 발행 대신 초안
            except Exception as e:  # noqa: BLE001 - 검증 실패 시 원문 유지 + 초안
                status, check_note = "draft", f", 사실검증 실패({str(e)[:60]}) → 검토 필요"
        article.content_html, link_count = related.clean_links(article.content_html, allowed_links)
        aligned = align_with_body(article)  # 마지막 안전장치: 제목·요약문 숫자가 본문과 다르면 바로잡음
        if aligned:
            check_note += f", 본문과 숫자 불일치 → {'·'.join(aligned)} 보정"
            if "제목(확인 필요)" in aligned:
                status = "draft"  # 제목 숫자가 본문과 다르면 자동 발행하지 않음
        post_id, link, image_note = publish(article, status, slug=keyword,
                                            cloudflare=(settings.cf_account_id, settings.cf_api_token),
                                            image_count=settings.image_count, schedule_times=settings.schedule_times)
        set_status(conn, keyword, "used", f"post {post_id}")
    kind = STATUS_LABEL.get(status, "초안 저장")
    cost = getattr(llm, "cost_usd", 0.0) + getattr(light, "cost_usd", 0.0)
    cost_note = f", AI 비용 약 ${cost:.2f}(≈{cost * 1400:,.0f}원)" if cost else ""
    return (f"[{kind}] '{article.title}' (키워드: {keyword}, 모델: {llm.model}, {image_note}, "
            f"내부 링크 {link_count}개{check_note}{cost_note}) {link}")


CACHE_DIR = "/var/cache/nginx/first-lab"


def _latest(orderby: str, field: str) -> str:
    return wp_cli("post", "list", "--post_type=post,page", "--post_status=publish", f"--orderby={orderby}",
                  "--order=DESC", "--posts_per_page=1", f"--field={field}").strip()


def purge_cache_if_changed(state: dict) -> None:
    """가장 최근 수정 시각이나 공개 시각이 바뀌었으면 nginx 페이지 캐시 삭제.
    (예약 글이 공개될 때는 수정 시각이 그대로일 수 있어 공개 시각도 본다)"""
    try:
        modified = _latest("modified", "post_modified")
        signature = f"{modified}|{_latest('date', 'post_date')}"
    except subprocess.CalledProcessError:
        return
    if modified and signature != state.get("cache_signature"):
        _sync_recent_cards(state.get("last_modified", ""))
        clear_cache()
        state["last_modified"], state["cache_signature"] = modified, signature


def run_due_wp_cron() -> None:
    """워드프레스 예약 작업(예약 글 공개 등) 실행. 방문이 적거나 캐시된 페이지만 보면 워드프레스가 스스로 못 돌림."""
    try:
        wp_cli("cron", "event", "run", "--due-now", "--quiet")
    except subprocess.CalledProcessError as e:
        print(f"[run] 워드프레스 예약 작업 실패: {e.stderr}", flush=True)


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


def yields_to_direct(settings: BotSettings, state: dict, today: str) -> bool:
    """월정액(직접 작성) 방식이고 오늘 직접 쓴 글이 올라왔으면 봇 자동 발행은 쉰다."""
    return settings.write_mode == "direct" and direct_post_today(state, today)


def direct_post_today(state: dict, today: str) -> bool:
    """예약 글쓰기 세션이 오늘 글을 올렸는지 (publish_queue 기록 기준)."""
    return any(e.get("ok") and str(e.get("time", "")).startswith(today) and "직접 작성 글:" in str(e.get("msg", ""))
               for e in state.get("log", []))


def daily_review(state: dict, settings: BotSettings, now: datetime) -> None:
    """하루 1회 상위 후보 사전 검토 → 설정 페이지에 근거 표시.
    API 봇: AI 안전 검사 + 경쟁도 / 월정액: 경쟁도만 (Gemini 무료. 안전 판단은 글쓰기 세션이 하고 제외 목록을 올림)"""
    today, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M")
    if state.get("review_date") == today or stamp < state.get("review_retry_at", ""):
        return
    if settings.write_mode == "direct":
        try:
            with connect() as conn:
                n = check_top_competition(conn, settings)
            add_log(state, True, f"후보 검토: 경쟁도 {n}건 조회 (월정액: 안전 판단은 글쓰기 세션이 함)")
        except Exception as e:  # noqa: BLE001
            add_log(state, False, f"후보 검토 실패: {e}")
        state["review_date"], state["dashboard_at"] = today, ""
        return
    if not settings.api_key:
        return
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


def fire_routine(state: dict, settings: BotSettings, label: str, text: str = "") -> None:
    """월정액: Claude 예약 작업 실행 (글은 그 세션이 써서 저장소로 올리고, 배포 때 사이트에 반영)."""
    try:
        url = routine.fire(settings.routine_fire_url, settings.routine_token, text)
        add_log(state, True, f"{label}: Claude 글쓰기 작업 시작 {url}")
    except routine.RoutineError as e:
        add_log(state, False, f"{label}: Claude 글쓰기 작업 실행 실패 - {e}")


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

    # 예약 글 공개 → 글이 발행·수정되면 페이지 캐시 비우기 (앱·관리자 화면에서 발행해도 1분 안에 반영)
    run_due_wp_cron()
    purge_cache_if_changed(state)

    # 관리자 '제외' 반영
    excluded = pop_excluded()
    if excluded:
        with connect() as conn:
            for kw in excluded:
                set_status(conn, kw, "excluded", "관리자 제외")

    daily_review(state, settings, now)
    stamp = now.strftime("%Y-%m-%d %H:%M")

    # 월정액: 직접 작성 시각이 되면 Claude 예약 작업 실행 (실패해도 같은 시각은 다시 안 함 → 예비 봇이 대신)
    if settings.write_mode == "direct":
        fired = state.get("direct_fired", {}).get(today, [])
        due = routine.due_slot(settings.direct_times, fired, now.replace(tzinfo=None))
        if due:
            state["direct_fired"] = {today: fired + [due]}  # 지난 날짜 기록은 버림
            fire_routine(state, settings, f"직접 작성 {due}")

    # 글 생성 (수동 요청 또는 자동 발행 시각)
    reason, slot = None, None
    if pop_manual_request():
        if settings.write_mode == "direct":  # 월정액: API 대신 Claude 가 씀
            fire_routine(state, settings, "수동 요청", routine.MANUAL_TEXT)
        else:
            state["manual_attempts"], state["manual_next"] = 0, ""
            reason = "수동"
    elif args.now or (state.get("manual_next") and stamp >= state["manual_next"]):
        reason = "수동(재시도)" if not args.now else "수동"
    slot_candidate = due_slot(state, settings, now)
    if reason is None and settings.enabled and slot_candidate is not None and stamp >= state.get("auto_next", ""):
        reason, slot = f"자동 {slot_candidate + 1}회차", slot_candidate

    if reason and slot is not None and yields_to_direct(settings, state, today):
        add_log(state, True, f"{reason} 건너뜀: 오늘 직접 작성 글이 이미 올라옴 (봇은 예비)")
        state["done"][today].append(state["plan"][today][slot])
        reason = None
    cap = settings.daily_cost_cap
    if reason and cap > 0 and spent_today(state) >= cap:
        add_log(state, False, f"{reason} 건너뜀: 오늘 AI 비용 ${spent_today(state):.2f} → 하루 상한 ${cap:.0f} 도달 (내일 다시)")
        state["manual_next"] = ""
        if slot is not None:
            state["done"][today].append(state["plan"][today][slot])
        reason = None
    if reason:
        used_llms: list = []
        try:
            msg = create_one(settings, used_llms)
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
        add_spend(state, sum(getattr(x, "cost_usd", 0.0) for x in used_llms))  # 실패해도 쓴 비용은 반영
        if slot is not None:  # 실패해도 같은 슬롯을 1분마다 재시도하지 않도록 완료 처리
            state["done"][today].append(state["plan"][today][slot])

    if reason or excluded or state.get("dashboard_at", "")[:13] != now.strftime("%Y-%m-%d %H"):
        refresh_dashboard(state, settings)  # 변화가 있거나 1시간마다
        state["dashboard_at"] = now.strftime("%Y-%m-%d %H:%M")

    if json.dumps(state, sort_keys=True, ensure_ascii=False) != before:
        save_state(state)


if __name__ == "__main__":
    main()
