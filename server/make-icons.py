"""사이트 아이콘(파비콘) 만들기: 파란 둥근 사각형 + 흰 'F' (퍼스트랩).

구글 검색 결과·브라우저 탭·휴대폰 홈 화면에 쓰인다. 글꼴 없이 도형으로 그려 작은 크기에서도 또렷하고,
다시 실행해도 같은 결과가 나온다. 모양·색을 바꾸려면 아래 상수만 고치고 다시 실행한 뒤 커밋.

사용: python3 server/make-icons.py  (Pillow 필요)
출력: wordpress/themes/first-lab/assets/icons/ (favicon.svg, favicon.ico, icon-192.png, apple-touch-icon.png)
      favicon.ico 는 배포(server/wp-setup.sh) 때 웹 루트(/favicon.ico)에도 복사된다.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "wordpress" / "themes" / "first-lab" / "assets" / "icons"

BG = "#1d4ed8"     # 배경 파랑
FG = "#ffffff"     # 글자 흰색
GRID = 48          # 설계 격자 (구글 권장: 48의 배수 크기)
RADIUS = 10        # 모서리 둥글기
BARS = [           # 'F' 를 이루는 막대 (x0, y0, x1, y1), 격자 단위
    (15, 11, 22, 37),    # 세로
    (15, 11, 34, 18),    # 위 가로
    (15, 21.5, 31, 27.5),  # 가운데 가로
]


def svg() -> str:
    rects = "".join(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}"/>' for x0, y0, x1, y1 in BARS)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}">'
            f'<rect width="{GRID}" height="{GRID}" rx="{RADIUS}" fill="{BG}"/>'
            f'<g fill="{FG}">{rects}</g></svg>\n')


def png(size: int, rounded: bool = True) -> Image.Image:
    """크게 그린 뒤 줄여서 가장자리를 매끄럽게."""
    big = size * 8
    k = big / GRID
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if rounded:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=RADIUS * k, fill=BG)
    else:  # 아이폰 홈 화면은 모서리를 스스로 둥글게 깎으므로 꽉 찬 사각형
        draw.rectangle((0, 0, big, big), fill=BG)
    for x0, y0, x1, y1 in BARS:
        draw.rectangle((x0 * k, y0 * k, x1 * k - 1, y1 * k - 1), fill=FG)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "favicon.svg").write_text(svg(), encoding="utf-8")
    # 크기마다 따로 그려 넣는다 (48 하나를 줄이면 16px 이 흐려짐)
    png(48).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=[png(16), png(32)])
    png(192).save(OUT / "icon-192.png", optimize=True)
    png(180, rounded=False).convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    print("아이콘 저장:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
