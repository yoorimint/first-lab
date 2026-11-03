"""키워드 창고 (서버 SQLite). 발굴한 키워드와 검색량·광고 지표·상태를 쌓는다.

status: new(미검사) → ok(안전 검사 통과) / rejected(부적합) / used(글 작성됨) / excluded(관리자 제외)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .naver_ads import KeywordStat

DB_PATH = Path("/var/lib/first-lab/bot/keywords.db")
REFRESH_DAYS = 7  # 이 기간이 지나 다시 조회되면 이전 검색량을 보관 (상승 감지용)

SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords (
    keyword      TEXT PRIMARY KEY,
    norm         TEXT NOT NULL,           -- 공백 제거·소문자 (중복 판단용)
    total        INTEGER NOT NULL,        -- 월간 검색량 (PC+모바일)
    prev_total   INTEGER,                 -- 이전 조회 때 검색량
    ad_comp      TEXT,                    -- 광고 경쟁도 (낮음/중간/높음)
    ad_clicks    REAL DEFAULT 0,          -- 월평균 광고 클릭 수
    status       TEXT NOT NULL DEFAULT 'new',
    reason       TEXT,                    -- 제외 사유 / 검색 의도 메모
    expanded     INTEGER NOT NULL DEFAULT 0, -- 출발점으로 확장했는지
    first_seen   TEXT NOT NULL,
    last_checked TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status ON keywords(status);
CREATE INDEX IF NOT EXISTS idx_total ON keywords(total);
"""


# 나중에 추가된 컬럼 (기존 DB 자동 보강)
MIGRATIONS = {
    "difficulty": "REAL",      # 경쟁 난이도 0~1 (구글 상위 사이트 기준)
    "comp_info": "TEXT",       # 경쟁 요약 + 상위 도메인
    "comp_checked": "TEXT",    # 경쟁도 조회 날짜
}


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(keywords)")}
    for column, kind in MIGRATIONS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE keywords ADD COLUMN {column} {kind}")


def norm(keyword: str) -> str:
    return "".join(keyword.split()).lower()


@contextmanager
def connect(path: Path = DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, stats: list[KeywordStat]) -> int:
    """새로 발견한 키워드 수를 반환."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    stale = (now - timedelta(days=REFRESH_DAYS)).strftime("%Y-%m-%d")
    new = 0
    for s in stats:
        row = conn.execute("SELECT total, last_checked FROM keywords WHERE keyword=?", (s.keyword,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO keywords (keyword, norm, total, ad_comp, ad_clicks, first_seen, last_checked)"
                " VALUES (?,?,?,?,?,?,?)",
                (s.keyword, norm(s.keyword), s.total, s.ad_competition, s.ad_clicks, today, today),
            )
            new += 1
        elif row["last_checked"] <= stale:  # 오래된 값 → 이전 값 보관 후 갱신
            conn.execute(
                "UPDATE keywords SET prev_total=total, total=?, ad_comp=?, ad_clicks=?, last_checked=? WHERE keyword=?",
                (s.total, s.ad_competition, s.ad_clicks, today, s.keyword),
            )
    return new


def set_status(conn: sqlite3.Connection, keyword: str, status: str, reason: str = "") -> None:
    conn.execute("UPDATE keywords SET status=?, reason=? WHERE keyword=?", (status, reason, keyword))


def summary(conn: sqlite3.Connection) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM keywords GROUP BY status")}
    return {
        "total": sum(counts.values()),
        "new_today": conn.execute("SELECT COUNT(*) FROM keywords WHERE first_seen=?", (today,)).fetchone()[0],
        "by_status": counts,
    }


def set_competition(conn: sqlite3.Connection, keyword: str, difficulty: float, info: str) -> None:
    conn.execute(
        "UPDATE keywords SET difficulty=?, comp_info=?, comp_checked=? WHERE keyword=?",
        (difficulty, info, datetime.now().strftime("%Y-%m-%d"), keyword),
    )


def competition_checks_today(conn: sqlite3.Connection) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    return conn.execute("SELECT COUNT(*) FROM keywords WHERE comp_checked=?", (today,)).fetchone()[0]
