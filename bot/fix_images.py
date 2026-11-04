"""엉터리 글자가 들어간 AI 사진 바꾸기 (배포 때 자동 실행, 여러 번 실행해도 안전).

wordpress/image-fixes.json: [{"old": "ai-1b47e7d5a0", "prompt": "글자 없는 새 장면 (영어)", "alt": "새 대체 텍스트", "why": "이유"}]
글 본문에 old 사진이 있으면 새로 만든 사진으로 주소를 바꾸고, 다 바꾼 뒤 옛 사진은 미디어에서 지운다.
이미 바뀐 글에는 old 가 없으니 다시 실행해도 아무 일도 안 한다.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import images
from .settings import wp_cli

FIXES = Path(__file__).resolve().parent.parent / "wordpress" / "image-fixes.json"


def _pattern(old: str) -> re.Pattern:
    """old 사진의 원본·축소본 주소 (…/ai-xxx.jpg, …/ai-xxx-300x169.jpg)."""
    return re.compile(r'https?://[^"\s]*/' + re.escape(old) + r'(?:-\d+x\d+)?\.jpg')


def uses(content: str, old: str) -> bool:
    return bool(_pattern(old).search(content))


def swap(content: str, old: str, new_url: str, alt: str = "") -> str:
    """사진 주소 교체. alt 를 주면 그 사진의 대체 텍스트도 새 장면에 맞게 바꾼다."""
    if alt:
        tag = re.compile(r'<img[^>]*src="' + _pattern(old).pattern + r'"[^>]*>')
        content = tag.sub(lambda m: re.sub(r'alt="[^"]*"', f'alt="{alt}"', m.group(0)), content)
    return _pattern(old).sub(new_url, content)


def _delete_attachment(old: str) -> None:
    att = wp_cli("post", "list", "--post_type=attachment", f"--name={old}", "--field=ID").split()
    if att:
        wp_cli("post", "delete", att[0], "--force")


def main() -> None:
    if not FIXES.exists():
        return
    fixes = json.loads(FIXES.read_text(encoding="utf-8"))
    ids = wp_cli("post", "list", "--post_type=post", "--post_status=any", "--field=ID").split()
    contents = {pid: wp_cli("post", "get", pid, "--field=post_content") for pid in ids}
    todo = [f for f in fixes if any(uses(c, f["old"]) for c in contents.values())]
    if not todo:
        return
    from .settings import load_settings

    s = load_settings()
    if not (s.cf_account_id and s.cf_api_token):
        print("[사진 교체] Cloudflare 설정 없음 → 건너뜀", flush=True)
        return
    for fix in todo:
        old = fix["old"]
        try:
            path = images.cloudflare_image(s.cf_account_id, s.cf_api_token, fix["prompt"])
            alt = fix.get("alt", "")
            _, new_url = images.import_media(path, alt or old, alt)
        except Exception as e:  # noqa: BLE001 - 사진 하나 실패가 배포를 막지 않게
            print(f"[사진 교체] {old} 생성 실패: {e}", flush=True)
            continue
        for pid, content in contents.items():
            if uses(content, old):
                contents[pid] = swap(content, old, new_url, alt)
                wp_cli("post", "update", pid, f"--post_content={contents[pid]}")
                print(f"[사진 교체] 글 {pid}: {old} → {new_url.rsplit('/', 1)[-1]} ({fix['why']})", flush=True)
        try:
            _delete_attachment(old)
        except subprocess.CalledProcessError as e:
            print(f"[사진 교체] {old} 옛 파일 삭제 실패: {e}", flush=True)


if __name__ == "__main__":
    main()
