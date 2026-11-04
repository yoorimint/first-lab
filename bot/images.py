"""글 이미지.

  - 대표 이미지: 제목 카드 자동 생성 (무료, 저작권 걱정 없음, 글마다 고유)
  - 본문 이미지: Cloudflare Workers AI (FLUX schnell) 무료 생성 1장 (설정에 Cloudflare 계정 ID·토큰이 있을 때)
    무료 한도: 하루 10,000 뉴런 (1024px 1장 약 58뉴런 → 하루 100장 이상)
이미지는 워드프레스 미디어에 직접 올려서 쓴다 (외부 링크 의존 X).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from .settings import WP_USER, wp_cli

TMP_DIR = Path("/var/lib/first-lab/tmp")  # 워드프레스(firstlab)가 읽을 수 있는 임시 폴더
FONT_PATHS = [
    "/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
]
SIZE = (1200, 630)  # SNS 공유·검색 미리보기 권장 비율
PALETTE = [  # (배경, 강조) - 키워드마다 색이 달라지게
    ("#1e3a5f", "#7fb3ff"), ("#2d4a3e", "#8fd6a8"), ("#4a2c40", "#f0a6ca"),
    ("#3d3a2a", "#f2d27a"), ("#2b2d42", "#a5b4fc"), ("#3a2e2a", "#f5a97f"),
]


def _font(size: int):
    from PIL import ImageFont

    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    """한글은 띄어쓰기 단위로, 너무 긴 단어는 글자 단위로 줄바꿈."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = word
        while draw.textlength(line, font=font) > max_width:  # 단어 하나가 너무 긴 경우
            cut = len(line)
            while cut > 1 and draw.textlength(line[:cut], font=font) > max_width:
                cut -= 1
            lines.append(line[:cut])
            line = line[cut:]
    if line:
        lines.append(line)
    return lines


def title_card(title: str, keyword: str, site_name: str = "퍼스트랩") -> Path:
    from PIL import Image, ImageDraw

    bg, accent = PALETTE[int(hashlib.md5(keyword.encode()).hexdigest(), 16) % len(PALETTE)]
    img = Image.new("RGB", SIZE, bg)
    draw = ImageDraw.Draw(img)
    w, h = SIZE
    margin = 170  # 테마가 가장자리를 잘라도 글자가 안 잘리도록 넉넉한 여백 + 가운데 정렬

    def center(text: str, y: int, font, fill: str) -> None:
        draw.text(((w - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)

    center(site_name, 70, _font(34), accent)                         # 사이트 이름
    for size in (72, 64, 56, 50):                                     # 제목이 3줄 안에 들어가는 크기
        font = _font(size)
        lines = _wrap(draw, title, font, w - margin * 2)
        if len(lines) <= 3:
            break
    lines = lines[:3]
    line_h = int(size * 1.35)
    y = (h - line_h * len(lines)) // 2
    for line in lines:
        center(line, y, font, "#ffffff")
        y += line_h
    center(f"# {keyword}", h - 100, _font(30), accent)
    draw.rectangle([w // 2 - 40, 125, w // 2 + 40, 129], fill=accent)  # 사이트 이름 아래 짧은 선

    return _save(img, f"card-{hashlib.md5(title.encode()).hexdigest()[:10]}.jpg")


def _save(img, name: str) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / name
    img.save(path, "JPEG", quality=88)
    shutil.chown(path, WP_USER, WP_USER)
    return path


CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"
CF_STEPS = 4            # 생성 단계 (많을수록 품질↑·비용↑, 최대 8)
BODY_SIZE = (1200, 675)  # 본문 이미지 16:9
STYLE = ("realistic editorial photo, natural light, clean composition, "
         "no text, no letters, no watermark, no logo")
# '글자 없이'라고 써도 이 모델은 서류·간판·화면이 나오면 엉터리 글자를 그린다 → 그런 장면은 아예 만들지 않는다
TEXT_PRONE = re.compile(
    r"\b(documents?|papers?|paperwork|receipts?|contracts?|forms?|schedules?|signs?|signage|calendars?|"
    r"labels?|certificates?|newspapers?|menus?|books?|envelopes?|bills?|statements?|invoices?|tickets?|"
    r"letters?|posters?|brochures?|leaflets?|websites?|charts?|printed|showing|displaying|interface|"
    r"kiosks?|airports?|terminals?|stations?|storefronts?|government building)\b",  # 안내판·간판이 따라 나오는 곳
    re.IGNORECASE)


def text_prone(prompt: str) -> str:
    """글자가 그려지기 쉬운 물건이 들어간 장면이면 그 단어, 아니면 빈 문자열."""
    m = TEXT_PRONE.search(prompt)
    return m.group(0) if m else ""


class QuotaExceeded(RuntimeError):
    """Cloudflare 하루 무료 한도 소진 (매일 00:00 UTC = 오전 9시 초기화). 못 만든 사진은 pending_images 가 나중에 채움."""


QUOTA_FILE = Path("/var/lib/first-lab/bot/cf-quota-until")  # 한도 소진 시 초기화 시각(UTC ISO)을 적어 둠 → 그때까지 호출 안 함


def _block_until_reset() -> None:
    from datetime import datetime, timedelta, timezone

    reset = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    try:
        QUOTA_FILE.write_text(reset.isoformat())
    except OSError:
        pass


def quota_blocked() -> bool:
    from datetime import datetime, timezone

    try:
        return datetime.now(timezone.utc) < datetime.fromisoformat(QUOTA_FILE.read_text().strip())
    except (OSError, ValueError):
        return False


def cloudflare_image(account_id: str, api_token: str, prompt: str) -> Path:
    """Cloudflare Workers AI 로 이미지 생성 → 16:9 로 잘라 저장."""
    import base64
    import io

    from PIL import Image, ImageOps

    if quota_blocked():
        raise QuotaExceeded("Cloudflare 하루 무료 한도 소진 상태 → 오전 9시 초기화 후 자동으로 채움")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{CF_MODEL}"
    body = json.dumps({"prompt": f"{prompt}. {STYLE}", "steps": CF_STEPS}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:  # 429 등: 한도 초과인지 알 수 있게 응답 내용도 남김
        body = e.read()[:300].decode(errors="replace")
        if e.code == 429 and "daily free allocation" in body:
            _block_until_reset()
            raise QuotaExceeded("Cloudflare 하루 무료 한도(10,000 뉴런) 소진 → 오전 9시 초기화 후 자동으로 채움") from None
        raise RuntimeError(f"Cloudflare HTTP {e.code}: {body[:200]}") from None
    if not data.get("success") or not data.get("result", {}).get("image"):
        raise RuntimeError(f"Cloudflare 이미지 생성 실패: {data.get('errors')}")
    img = Image.open(io.BytesIO(base64.b64decode(data["result"]["image"]))).convert("RGB")
    img = ImageOps.fit(img, BODY_SIZE)  # 가운데 기준으로 16:9 자르기
    return _save(img, f"ai-{hashlib.md5(prompt.encode()).hexdigest()[:10]}.jpg")


def import_media(path: Path, title: str, alt: str) -> tuple[int, str]:
    """워드프레스 미디어에 올리고 (첨부 ID, 주소) 반환. 임시 파일은 삭제."""
    att_id = int(wp_cli("media", "import", str(path), f"--title={title}", f"--alt={alt}", "--porcelain").strip())
    url = wp_cli("eval", f"echo wp_get_attachment_url({att_id});").strip()
    path.unlink(missing_ok=True)
    return att_id, url


def delete_attachment(name: str) -> None:
    """미디어에서 사진 지우기 (파일 이름 = 첨부 이름, 예: ai-1b47e7d5a0). 없으면 조용히 넘어감."""
    import subprocess

    ids = wp_cli("post", "list", "--post_type=attachment", f"--name={name}", "--field=ID").split()
    if ids:
        try:
            wp_cli("post", "delete", ids[0], "--force")
        except subprocess.CalledProcessError:
            pass


if __name__ == "__main__":
    # 점검용: python -m bot.images  → Cloudflare 이미지 1장 생성 테스트 (워드프레스에는 올리지 않음)
    from .settings import load_settings

    s = load_settings()
    if not (s.cf_account_id and s.cf_api_token):
        raise SystemExit("설정에 Cloudflare 계정 ID / API 토큰이 없습니다")
    print("생성됨:", cloudflare_image(s.cf_account_id, s.cf_api_token, "a cozy desk with a laptop and coffee"))
