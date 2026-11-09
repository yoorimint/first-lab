#!/usr/bin/env bash
# 전체 글을 한 편씩 읽는 보정 — 배포를 기다리게 하지 않게 따로 실행 (bot-setup.sh·quick-deploy.sh 가 flock 잠금으로 부름, 11/9).
#   패치 반영·블록 형식(fix_posts) → 반영 확인(patch_check) → 본문 사진 정리(migrate_photos) → 사진 기다리는 글 표시(pending_images)
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
P=/opt/first-lab-venv/bin/python
cd "$REPO_DIR"
for mod in bot.fix_posts bot.patch_check bot.migrate_photos bot.pending_images; do
  echo "== $mod $(date '+%H:%M:%S')"
  "$P" -m "$mod" || echo "$mod 실패 (다음 배포 때 다시)"
done
echo "== 끝 $(date '+%H:%M:%S')"
