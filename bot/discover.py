"""오늘의 키워드 수집 (하루 1번, 네이버 키워드 도구 무료 → 매일 최대한 수집).

  1) 출발 단어: 구글 급상승 검색어 + 씨앗 단어 전부(seed_words.py) + 우선 주제
  2) 롱테일 조합: 오늘 나온 큰 키워드 × 수식어 전부(MODIFIERS) + 올해·내년 연도의 실제 검색량 확인 (남은 호출 전부)
  3) 정리: 8일 지난 후보는 삭제 (7일 전 검색량과 비교해 상승세 감지용으로만 보관)
     이미 쓴 키워드·부적합·제외 기록은 중복 방지용으로 계속 보관

사용: python -m bot.discover [--calls 60]
"""
from __future__ import annotations

import argparse
import importlib
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from .keyword_db import connect, norm, summary, upsert
from .keyword_filter import MODIFIERS
from .naver_ads import ads_client
from .seed_words import FOCUS_SEEDS, SEED_WORDS
from .seeds import google_trends_kr

BATCH = 5              # API 1회당 출발 키워드 수 (API 최대값)
PAUSE_SECONDS = 0.3    # 호출 간격
def _combos(head: str) -> list[str]:
    """큰 키워드 × 모든 수식어 + 올해·내년 연도 (내년 키워드는 글이 적을 때 미리 선점)."""
    n, year = norm(head), datetime.now().year
    combos = [n + m for m in MODIFIERS if m not in n]
    combos += [f"{y}{n}" for y in (year, year + 1) if str(y) not in n]
    return list(dict.fromkeys(combos))
HEAD_MIN_VOLUME = 3000  # 롱테일 조합을 만들 '큰 키워드' 기준
KEEP_DAYS = 8           # 후보 보관 기간 (상승세 비교용)
STAMP_FILE = Path("/var/lib/first-lab/bot/discover-date-v2")  # 마지막 수집 날짜 (하루 1회 보장)


GOLD_HEAD_MIN = 1000    # 황금·좋음(황금지수 0.1 이상) 키워드는 이 검색량부터 롱테일 조합을 만든다


def _modifier_heads(conn, today: str, limit_heads: int) -> list[str]:
    """롱테일 조합을 만들 큰 키워드: 황금·좋음 키워드 먼저(그 주변 롱테일도 글이 적을 가능성), 다음 돈·계산기 주제, 검색량 순."""
    from .naver_hub import GOOD_INDEX, load
    from .select_keyword import topic_weight
    load(conn)  # naver_signals 표 준비
    rows = conn.execute(
        "SELECT k.keyword, k.norm, k.total, n.hits, n.docs FROM keywords k LEFT JOIN naver_signals n ON n.norm = k.norm"
        " WHERE k.mod_done=0 AND k.status NOT IN ('rejected','excluded') AND ("
        " (k.last_checked=? AND k.total>=?) OR (n.docs IS NOT NULL AND k.total >= n.docs * ? AND k.total>=?))",
        (today, HEAD_MIN_VOLUME, GOOD_INDEX, GOLD_HEAD_MIN)).fetchall()
    rows = sorted(rows, key=lambda r: (r["docs"] is not None and r["total"] >= r["docs"] * GOOD_INDEX, topic_weight(r["norm"]), r["total"]),
                  reverse=True)
    return [r["keyword"] for r in rows[:limit_heads]]


# 중심 주제 씨앗 늘리기 (11/7 운영자 '중심 주제 단어가 200여 개밖에 안 되나? 몇천 개는 되지 않나?'):
# 창고에 모인 중심 주제 키워드(검색 많은 순)를 다음 수집의 출발 단어로 — 날마다 SNOWBALL_CALLS×5 개, 같은 단어는 REST_DAYS 뒤 다시.
SNOWBALL_CALLS = 40
SNOWBALL_MIN = 300      # 이 검색량 이상인 키워드만 출발 단어로
SNOWBALL_REST_DAYS = 14


def focus_snowball(conn, today: str, limit: int) -> list[str]:
    from .select_keyword import on_focus
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=SNOWBALL_REST_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT k.keyword, k.norm FROM keywords k LEFT JOIN seeds s ON s.word = k.keyword"
        " WHERE k.status NOT IN ('rejected','excluded') AND k.total >= ? AND (s.used_date IS NULL OR s.used_date <= ?)"
        " ORDER BY k.total DESC", (SNOWBALL_MIN, cutoff)).fetchall()
    out = [r["keyword"] for r in rows if on_focus(r["norm"]) and r["keyword"] not in SEED_WORDS]
    return list(dict.fromkeys(out))[:limit]


def secrets_expiring(today=None, days: int = 30) -> list[str]:
    """비밀값 파일의 NAME_EXPIRES=YYYY-MM-DD 중 days 일 안에 끝나는 것 (값은 읽지 않음)."""
    from datetime import date, timedelta
    from .config import SECRETS_FILE, load_secrets
    today = today or date.today()
    try:
        secrets = load_secrets(SECRETS_FILE) if SECRETS_FILE.exists() else {}
    except OSError:
        return []
    out = []
    for key, value in secrets.items():
        if key.endswith("_EXPIRES"):
            try:
                if date.fromisoformat(value.strip()) - today <= timedelta(days=days):
                    out.append(f"{key[:-8]}({value.strip()})")
            except ValueError:
                continue
    return sorted(out)


def discover(calls: int, extra_seeds: list[str] | None = None) -> dict:
    ads = ads_client()
    stats = {"calls": 0, "new": 0, "errors": 0, "trends": 0}
    today = datetime.now().strftime("%Y-%m-%d")

    def run_batch(conn, batch: list[str]) -> bool:
        """한 묶음 조회. 계속 진행해도 되면 True."""
        for attempt in range(2):
            try:
                stats["new"] += upsert(conn, ads.keyword_stats(batch))
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 0:  # 호출 과다 → 잠시 쉬고 한 번 더
                    time.sleep(30)
                    continue
                stats["errors"] += 1
                if e.code in (401, 403):
                    return False
                break
            except Exception:  # noqa: BLE001 - 응답 끊김·JSON 오류 하나가 그날 수집 전체를 멈추지 않게 (11/5 검토)
                stats["errors"] += 1
                break
        stats["calls"] += 1
        time.sleep(PAUSE_SECONDS)
        return True

    with connect() as conn:
        # 1) 출발 단어 전부: 급상승어 + 우선 주제 + 씨앗
        trends = google_trends_kr()
        stats["trends"] = len(trends)
        snowball = focus_snowball(conn, today, SNOWBALL_CALLS * BATCH)
        stats["snowball"] = len(snowball)
        words = list(dict.fromkeys(trends + (extra_seeds or []) + list(FOCUS_SEEDS) + snowball + SEED_WORDS))
        for i in range(0, len(words), BATCH):
            if stats["calls"] >= calls or not run_batch(conn, words[i:i + BATCH]):
                break
        conn.executemany("INSERT OR REPLACE INTO seeds (word, used_date) VALUES (?,?)",
                         [(w, today) for w in words if w in SEED_WORDS or w in snowball])
        conn.commit()
        print(f"[수집] 출발 단어 {len(words)}개 (급상승 {len(trends)}): {stats}", flush=True)

        # 2) 롱테일 조합: 오늘 나온 큰 키워드 × 수식어
        per_head = -(-len(_combos("가")) // BATCH)
        for head in _modifier_heads(conn, today, max(1, (calls - stats["calls"]) // per_head)):
            if stats["calls"] >= calls:
                break
            combos = _combos(head)
            for i in range(0, len(combos), BATCH):
                if not run_batch(conn, combos[i:i + BATCH]):
                    break
            conn.execute("UPDATE keywords SET mod_done=1 WHERE keyword=?", (head,))
            conn.commit()
        print(f"[수집] 롱테일 조합 완료: {stats}", flush=True)

        # 3) 정리: 오래된 후보 삭제 (쓴 것·부적합·제외 기록은 보관)
        cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        from .naver_hub import GOOD_INDEX, load
        load(conn)
        stats["pruned"] = conn.execute(  # 황금·좋음(검색 1회당 글 1개 미만)으로 확인된 키워드는 오래돼도 남김 (글감 후보)
            "DELETE FROM keywords WHERE status IN ('new','ok') AND last_checked < ? AND norm NOT IN ("
            " SELECT n.norm FROM naver_signals n JOIN keywords k ON k.norm = n.norm"
            " WHERE n.docs IS NOT NULL AND k.total >= n.docs * ?)",
            (cutoff, GOOD_INDEX)).rowcount
        try:  # 지운 키워드의 네이버 신호도 지움 — 안 그러면 하루 1만9천 줄씩 끝없이 쌓여 봇이 느려짐 (11/5 검토)
            conn.execute("DELETE FROM naver_signals WHERE norm NOT IN (SELECT norm FROM keywords)")
        except Exception:  # noqa: BLE001 - 표가 아직 없으면 그냥 넘어감
            pass
        conn.commit()
        info = summary(conn)
    return {**stats, **info}


def _apply_search_console() -> str:
    from . import search_console

    if not search_console.available():
        return "서치콘솔: 미연동"
    try:
        rows = search_console.fetch()
        with connect() as conn:
            r = search_console.update_signals(conn, rows)
        popular = search_console.save_popular(rows)
        return (f"서치콘솔: 검색어 {r['rows']}건 → 기회 키워드 {r['opportunity']}, "
                f"잘 되는 주제 {r['good_head']}, 안 되는 주제 {r['bad_head']}, "
                f"홈 대표 글 {urllib.parse.unquote(popular) if popular else '최신 글'}")
    except Exception as e:  # noqa: BLE001 - 서치콘솔 오류가 수집을 막지 않게
        return f"서치콘솔 오류: {e}"


def _refresh_hub_signals() -> str:
    """상위 후보의 블로그 문서 수·검색 성수기 (bot/naver_hub.py, 하루 호출 상한 안). 실패해도 수집은 성공."""
    try:
        from . import naver_hub
        return naver_hub.run_daily()  # 먼저 황금 키워드 찾기 → 그 결과가 후보 순위에 반영된 뒤 성수기
    except Exception as e:  # noqa: BLE001
        return f"네이버 API HUB 실패: {e}"


def already_ran_today(today: str | None = None) -> bool:
    """오늘 이미 수집했는지. 배포 때 시작한 수집과 새벽 4시 예약 수집이 같은 날 두 번 돌던 일 방지 (11/5)."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    try:
        return STAMP_FILE.read_text().strip() == today
    except OSError:
        return False


def main() -> None:
    """systemd 일일 작업 진입점: 결과를 봇 기록에 남긴다."""
    from .settings import load_settings
    from .state import add_log, load_state, save_state

    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, help="API 호출 수 (생략 시 설정 페이지 값)")
    parser.add_argument("--force", action="store_true", help="오늘 이미 수집했어도 다시")
    args = parser.parse_args()
    if not args.force and already_ran_today():
        print("오늘 수집 완료 → 건너뜀", flush=True)
        return
    settings = load_settings()
    started = time.time()
    try:
        r = discover(args.calls or settings.discover_calls, settings.hint_keywords)
        msg = (f"오늘의 키워드 수집: API {r['calls']}회, 급상승어 {r['trends']}개 포함, "
               f"새 키워드 {r['new']:,}개 (보관 {r['total']:,}개, {int(time.time() - started)}초)")
        ok = True
        msg += " / " + _apply_search_console()
        msg += " / " + _refresh_hub_signals()
    except Exception as e:  # noqa: BLE001
        msg, ok = f"키워드 수집 실패: {e}", False
    print(msg, flush=True)
    if ok:  # 실패한 날은 다음 배포·타이머 때 다시 수집 (11/5 검토: 실패해도 '오늘 완료'가 찍혀 다시 안 함)
        STAMP_FILE.write_text(datetime.now().strftime("%Y-%m-%d"))
    failures = []
    for label, module, func in (("법령 감시", "law_watch", "run"), ("새 혜택 감시", "benefit_watch", "run"),
                                ("금리 갱신", "bok_api", "run"), ("계산기 연초 값·근거 법령 확인", "calc_watch", "run"),
                                ("공개 글 근거 다시 보기", "recheck", "run")):
        try:  # 하나가 실패해도 나머지는 계속, 실패는 봇 기록(/maintenance)에 남김 — 12/5 뒤엔 journal 을 볼 사람이 없음
            print(getattr(importlib.import_module(f".{module}", __package__), func)(), flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{label} 실패: {e}", flush=True)
            failures.append(f"{label} 실패: {str(e)[:150]}")
    state = load_state()  # 수집 중 봇이 바꾼 상태를 덮어쓰지 않도록 다시 읽음
    add_log(state, ok, msg)
    try:  # 검색 성과 요약 (/maintenance 'search', bot/search_console.record)
        from .search_console import record as record_search
        line = record_search(state)
        if line:
            add_log(state, True, line)
    except Exception as e:  # noqa: BLE001
        failures.append(f"검색 성과 요약 실패: {str(e)[:150]}")
    for line in failures:
        add_log(state, False, line)
    expiring = secrets_expiring()  # 비밀값 이름_EXPIRES=YYYY-MM-DD 를 set-secrets.sh 로 넣어 두면 30일 전부터 알림 (/health → 메일)
    state["secrets_expiring"] = expiring
    if expiring:
        add_log(state, False, f"곧 만료되는 키: {', '.join(expiring)} — 서버 set-secrets.sh 로 새 값과 새 만료일")
    # 발행된 글 갱신 (서치콘솔 반영 직후, 주당 한도 안에서 하루 최대 1개)
    from .refresh import run_weekly

    for line in run_weekly(settings, state):
        add_log(state, "실패" not in line, line)
    state["review_date"] = ""      # 오늘 후보로 사전 검토
    state["dashboard_at"] = ""
    save_state(state)


if __name__ == "__main__":
    main()
