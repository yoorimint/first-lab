"""워드프레스 관리자 > 설정 > 퍼스트랩 봇 의 값을 읽는다 (WP-CLI 사용).

API 키는 워드프레스 설정 값을 우선 사용하고, 비어 있으면 서버의
/etc/first-lab/secrets.env 값을 사용한다.
"""
from __future__ import annotations

import json
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


@dataclass
class BotSettings:
    enabled: bool = False
    post_status: str = "draft"
    posts_per_day: int = 2
    provider: str = "gemini"
    model: str = ""
    hint_keywords: list[str] = field(default_factory=list)
    min_volume: int = 500
    max_volume: int = 30000
    blocked_words: list[str] = field(default_factory=list)
    target_length: int = 3000
    extra_prompt: str = ""
    api_key: str = ""  # 선택된 제공사의 키

    @classmethod
    def from_option(cls, raw: dict, secrets: dict[str, str]) -> "BotSettings":
        provider = raw.get("provider") or "gemini"
        wp_field, env_name = _KEY_SOURCES.get(provider, ("", ""))
        return cls(
            enabled=bool(int(raw.get("enabled") or 0)),
            post_status=raw.get("post_status") or "draft",
            posts_per_day=int(raw.get("posts_per_day") or 2),
            provider=provider,
            model=(raw.get("model") or "").strip(),
            hint_keywords=_lines(raw.get("hint_keywords", "")),
            min_volume=int(raw.get("min_volume") or 0),
            max_volume=int(raw.get("max_volume") or 0),
            blocked_words=_lines(raw.get("blocked_words", "")),
            target_length=int(raw.get("target_length") or 3000),
            extra_prompt=raw.get("extra_prompt") or "",
            api_key=(raw.get(wp_field) or "").strip() or secrets.get(env_name, ""),
        )


def wp_cli(*args: str) -> str:
    """워드프레스 파일 소유자 권한으로 WP-CLI 실행."""
    cmd = ["runuser", "-u", WP_USER, "--", "wp", f"--path={WP_PATH}", *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def load_settings() -> BotSettings:
    try:
        raw = json.loads(wp_cli("option", "get", OPTION, "--format=json") or "{}")
    except subprocess.CalledProcessError:
        raw = {}  # 아직 한 번도 저장하지 않은 경우
    return BotSettings.from_option(raw, load_secrets())


if __name__ == "__main__":
    # 점검용: 키 값은 숨기고 설정만 출력
    s = load_settings()
    shown = {k: v for k, v in vars(s).items() if k != "api_key"}
    shown["api_key"] = f"있음 (끝자리 {s.api_key[-4:]})" if s.api_key else "없음"
    for k, v in shown.items():
        print(f"{k:15} {v}")
