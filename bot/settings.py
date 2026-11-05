"""워드프레스 관리자 > 설정 > 퍼스트랩 봇 의 값을 읽는다 (WP-CLI 사용).

API 키는 워드프레스 설정 값을 우선 사용하고, 비어 있으면 서버의
/etc/first-lab/secrets.env 값을 사용한다.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field

from .config import load_secrets

WP_PATH = "/var/www/first-lab"
WP_USER = "firstlab"
OPTION = "firstlab_bot"

# 키 이름: (워드프레스 설정 필드, secrets.env 이름)
_KEY_SOURCES = {
    "gemini": ("gemini_key", "GEMINI_API_KEY"),
    "anthropic": ("anthropic_key", "ANTHROPIC_API_KEY"),
    "openai": ("openai_key", "OPENAI_API_KEY"),
}


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _times(text: str) -> list[str]:
    """'09:00', '9:00 20:30', '9시 30분' 등 → ['09:00', '20:30'] (형식이 틀린 부분은 무시)."""
    times = set()
    for h, m in re.findall(r"(\d{1,2})\s*[:시]\s*(\d{1,2})?", text or ""):
        h, m = int(h), int(m or 0)
        if 0 <= h < 24 and 0 <= m < 60:
            times.add(f"{h:02d}:{m:02d}")
    return sorted(times)


@dataclass
class BotSettings:
    write_mode: str = "direct"  # direct = 월정액 직접 작성(봇은 예비) | bot = API 봇이 작성
    maint_times: list[str] = field(default_factory=lambda: ["06:10"])  # 매일 점검 시각 (비우면 안 함)
    direct_enabled: bool = True  # '글 작성 시각 사용' (끄면 정해진 시각에 자동으로 쓰지 않음)
    direct_times: list[str] = field(default_factory=lambda: ["07:40"])  # 글 작성 시각 (월정액: Claude, API 봇: API)
    routine_fire_url: str = ""  # 월정액: Claude 예약 작업 실행 주소·토큰 (secrets.env, bot/routine.py)
    routine_token: str = ""
    enabled: bool = False
    post_status: str = "draft"   # draft | publish | schedule(예약 발행)
    schedule_times: list[str] = field(default_factory=lambda: ["09:00"])  # 예약 발행 공개 시각
    jitter_minutes: int = 30    # 예약 공개 시각 ± 랜덤(분)
    provider: str = "gemini"
    model: str = ""
    hint_keywords: list[str] = field(default_factory=list)
    discover_calls: int = 300
    min_volume: int = 500
    max_volume: int = 30000
    blocked_words: list[str] = field(default_factory=list)
    target_length: int = 3000
    extra_prompt: str = ""
    persona: str = ""
    api_key: str = ""  # 선택된 제공사의 키
    gemini_key: str = ""  # 경쟁도 조회(구글 검색 연동)용 - 글쓰기 AI 와 무관하게 사용
    cf_account_id: str = ""   # 본문 AI 이미지 (Cloudflare Workers AI)
    cf_api_token: str = ""
    fact_check: bool = True    # 자동 사실 검증
    refresh_per_week: int = 5  # 발행 글 갱신 주당 최대 개수
    competition_checks: int = 15  # 하루 경쟁도 조회 한도 (Gemini 무료 한도가 작음)
    daily_cost_cap: float = 3.0   # 하루 AI 비용 상한 (달러, 0 = 없음)

    @classmethod
    def from_option(cls, raw: dict, secrets: dict[str, str]) -> "BotSettings":
        provider = raw.get("provider") or "gemini"
        wp_field, env_name = _KEY_SOURCES.get(provider, ("", ""))
        return cls(
            write_mode="bot" if raw.get("write_mode") == "bot" else "direct",
            maint_times=_times(raw.get("maint_times", "06:10")),
            direct_enabled=bool(int(raw.get("direct_enabled", 1) or 0)),
            direct_times=_times(raw.get("direct_times", "07:40")),
            routine_fire_url=secrets.get("CLAUDE_ROUTINE_FIRE_URL", ""),
            routine_token=secrets.get("CLAUDE_ROUTINE_TOKEN", ""),
            enabled=bool(int(raw.get("enabled") or 0)),
            post_status=raw.get("post_status") or "draft",
            schedule_times=_times(raw.get("schedule_times", "09:00")) or ["09:00"],
            jitter_minutes=int(raw.get("jitter_minutes") or 0),
            provider=provider,
            model=(raw.get("model") or "").strip(),
            hint_keywords=_lines(raw.get("hint_keywords", "")),
            discover_calls=int(raw.get("discover_calls") or 300),
            min_volume=int(raw.get("min_volume") or 0),
            max_volume=int(raw.get("max_volume") or 0),
            blocked_words=_lines(raw.get("blocked_words", "")),
            target_length=int(raw.get("target_length") or 3000),
            extra_prompt=raw.get("extra_prompt") or "",
            persona=raw.get("persona") or "",
            api_key=(raw.get(wp_field) or "").strip() or secrets.get(env_name, ""),
            gemini_key=(raw.get("gemini_key") or "").strip() or secrets.get("GEMINI_API_KEY", ""),
            competition_checks=int(raw.get("competition_checks") or 15),
            daily_cost_cap=float(raw.get("daily_cost_cap") if raw.get("daily_cost_cap") not in (None, "") else 3),
            cf_account_id=(raw.get("cf_account_id") or "").strip() or secrets.get("CF_ACCOUNT_ID", ""),
            cf_api_token=(raw.get("cf_api_token") or "").strip() or secrets.get("CF_API_TOKEN", ""),
            fact_check=bool(int(raw.get("fact_check", 1) or 0)),
            refresh_per_week=int(raw.get("refresh_per_week") if raw.get("refresh_per_week") not in (None, "") else 5),
        )


def wp_cli(*args: str, stdin: str | None = None) -> str:
    """워드프레스 파일 소유자 권한으로 WP-CLI 실행. stdin: 값을 표준 입력으로 (명령줄 인자 한도 128KB 를 넘는 값)."""
    cmd = ["runuser", "-u", WP_USER, "--", "wp", f"--path={WP_PATH}", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, input=stdin)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd[:7], result.stdout, result.stderr.strip()[:300])
    return result.stdout


def wp_meta(post_id, key: str, wp=None) -> str:
    """글 메타 값. 없으면 '' — WP-CLI 'post meta get' 은 값이 없으면 오류로 끝남 (11/5: 처음 표시하는 글에서 표시·보류 횟수가 실패)."""
    try:
        return (wp or wp_cli)("post", "meta", "get", str(post_id), key).strip()
    except subprocess.CalledProcessError:
        return ""


def option_missing(e: subprocess.CalledProcessError) -> bool:
    """wp option get 실패가 '옵션이 아직 없음'인지 (그 밖의 실패는 일시 오류)."""
    return "Could not get" in str(e.stderr or "") or "does not exist" in str(e.stderr or "")


def load_settings() -> BotSettings:
    try:
        raw = json.loads(wp_cli("option", "get", OPTION, "--format=json") or "{}")
    except subprocess.CalledProcessError as e:
        if not option_missing(e):
            raise  # 잠깐 못 읽은 것(잠금·배포 중)을 기본 설정으로 착각해 공개 기록을 지우지 않게 (11/5 검토)
        raw = {}  # 아직 한 번도 저장하지 않은 경우
    return BotSettings.from_option(raw, load_secrets())


if __name__ == "__main__":
    # 점검용: 키 값은 숨기고 설정만 출력
    s = load_settings()
    shown = {k: v for k, v in vars(s).items() if not k.endswith(("key", "token"))}
    for k in ("api_key", "gemini_key", "cf_api_token", "routine_token"):
        v = getattr(s, k)
        shown[k] = f"있음 (끝자리 {v[-4:]})" if v else "없음"
    for k, v in shown.items():
        print(f"{k:15} {v}")
