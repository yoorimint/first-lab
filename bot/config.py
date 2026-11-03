"""비밀값 로딩. 키는 저장소가 아닌 서버의 /etc/first-lab/secrets.env 에만 둔다."""
import os
from pathlib import Path

SECRETS_FILE = Path(os.environ.get("FIRSTLAB_SECRETS", "/etc/first-lab/secrets.env"))


def load_secrets(path: Path = SECRETS_FILE) -> dict[str, str]:
    """KEY=VALUE 형식 파일을 읽는다. 빈 줄과 # 주석은 무시."""
    secrets: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        secrets[key.strip()] = value.strip()
    return secrets


def require(secrets: dict[str, str], *keys: str) -> list[str]:
    missing = [k for k in keys if not secrets.get(k)]
    if missing:
        raise KeyError(f"{SECRETS_FILE} 에 다음 값이 없습니다: {', '.join(missing)}")
    return [secrets[k] for k in keys]
