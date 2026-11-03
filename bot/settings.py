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
    enabled: bool = False
    post_status: str = "draft"
    publish_times: list[str] = field(default_factory=lambda: ["09:00", "20:00"])
    jitter_minutes: int = 30
    provider: str = "gemini"
    model: str = ""
    hint_keywords: list[str] = field(default_factory=list)
    discover_calls: int = 300
    min_volume: int = 500
    max_volume: int = 30000
    blocked_words: list[str] = field(default_factory=list)
    target_length: int = 3000
    extra_prompt: str = ""
    api_key: str = ""  # 선택된 제공사의 키
    gemini_key: str = ""  # 경쟁도 조회(구글 검색 연동)용 - 글쓰기 AI 와 무관하게 사용
    competition_checks: int = 15  # 하루 경쟁도 조회 한도 (Gemini 무료 한도가 작음)

    @classmethod
    def from_option(cls, raw: dict, secrets: dict[str, str]) -> "BotSettings":
        provider = raw.get("provider") or "gemini"
        wp_field, env_name = _KEY_SOURCES.get(provider, ("", ""))
        return cls(
            enabled=bool(int(raw.get("enabled") or 0)),
            post_status=raw.get("post_status") or "draft",
            publish_times=_times(raw.get("publish_times", "09:00\n20:00")),
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
            api_key=(raw.get(wp_field) or "").strip() or secrets.get(env_name, ""),
            gemini_key=(raw.get("gemini_key") or "").strip() or secrets.get("GEMINI_API_KEY", ""),
            competition_checks=int(raw.get("competition_checks") or 15),
        )


def wp_cli(*args: str) -> str:
    """워드프레스 파일 소유자 권한으로 WP-CLI 실행."""
    cmd = ["runuser", "-u", WP_USER, "--", "wp", f"--path={WP_PATH}", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd[:7], result.stdout, result.stderr.strip()[:300])
    return result.stdout


def load_settings() -> BotSettings:
    try:
        raw = json.loads(wp_cli("option", "get", OPTION, "--format=json") or "{}")
    except subprocess.CalledProcessError:
        raw = {}  # 아직 한 번도 저장하지 않은 경우
    return BotSettings.from_option(raw, load_secrets())


if __name__ == "__main__":
    # 점검용: 키 값은 숨기고 설정만 출력
    s = load_settings()
    shown = {k: v for k, v in vars(s).items() if not k.endswith("key")}
    for k in ("api_key", "gemini_key"):
        v = getattr(s, k)
        shown[k] = f"있음 (끝자리 {v[-4:]})" if v else "없음"
    for k, v in shown.items():
        print(f"{k:15} {v}")
