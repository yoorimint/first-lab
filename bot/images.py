"""글 이미지.

  - 대표 이미지: 제목 카드 자동 생성 (무료, 저작권 걱정 없음, 글마다 고유)
  - 본문 사진: Pexels 무료 사진 1장 (설정에 Pexels 키가 있을 때만, 상업적 사용 무료)
이미지는 워드프레스 미디어에 직접 올려서 쓴다 (외부 링크 의존 X).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.parse
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
    draw.rectangle([0, 0, 18, h], fill=accent)                       # 왼쪽 강조 띠
    draw.text((80, 60), site_name, font=_font(34), fill=accent)       # 사이트 이름

    for size in (76, 68, 60, 52):                                     # 제목이 4줄 안에 들어가는 크기
        font = _font(size)
        lines = _wrap(draw, title, font, w - 160)
        if len(lines) <= 4:
            break
    lines = lines[:4]
    line_h = int(size * 1.35)
    y = (h - line_h * len(lines)) // 2 + 10
    for line in lines:
        draw.text((80, y), line, font=font, fill="#ffffff")
        y += line_h
    draw.text((80, h - 90), f"# {keyword}", font=_font(30), fill=accent)

    return _save(img, f"card-{hashlib.md5(title.encode()).hexdigest()[:10]}.jpg")


def _save(img, name: str) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / name
    img.save(path, "JPEG", quality=88)
    shutil.chown(path, WP_USER, WP_USER)
    return path


def pexels_photo(api_key: str, query: str) -> dict | None:
    """Pexels 에서 가로 사진 1장 검색 → 내려받기. {'path','photographer','url'}"""
    params = urllib.parse.urlencode({"query": query, "per_page": 5, "orientation": "landscape"})
    req = urllib.request.Request(f"https://api.pexels.com/v1/search?{params}", headers={"Authorization": api_key})
    with urllib.request.urlopen(req, timeout=20) as resp:
        photos = json.load(resp).get("photos", [])
    if not photos:
        return None
    photo = photos[0]
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / f"pexels-{photo['id']}.jpg"
    img_req = urllib.request.Request(photo["src"]["large"], headers={"User-Agent": "first-lab-bot"})
    with urllib.request.urlopen(img_req, timeout=30) as resp:
        path.write_bytes(resp.read())
    shutil.chown(path, WP_USER, WP_USER)
    return {"path": path, "photographer": photo.get("photographer", ""), "url": photo.get("url", "")}


def import_media(path: Path, title: str, alt: str) -> tuple[int, str]:
    """워드프레스 미디어에 올리고 (첨부 ID, 주소) 반환. 임시 파일은 삭제."""
    att_id = int(wp_cli("media", "import", str(path), f"--title={title}", f"--alt={alt}", "--porcelain").strip())
    url = wp_cli("eval", f"echo wp_get_attachment_url({att_id});").strip()
    path.unlink(missing_ok=True)
    return att_id, url


def image_block(url: str, alt: str, caption: str = "") -> str:
    cap = f'<figcaption class="wp-element-caption">{caption}</figcaption>' if caption else ""
    return (f'<!-- wp:image {{"sizeSlug":"large"}} -->\n'
            f'<figure class="wp-block-image size-large"><img src="{url}" alt="{alt}"/>{cap}</figure>\n'
            f'<!-- /wp:image -->')
