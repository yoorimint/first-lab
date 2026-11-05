#!/usr/bin/env bash
# first-lab 백업 (매일): 워드프레스 DB(SQLite) + 업로드 이미지 + 봇 설정·키워드 창고 → 14일 보관
# ⚠️ 같은 서버 안의 백업이라 서버 자체 장애에는 대비하지 못함 (외부 보관은 별도)
set -euo pipefail
DEST=/var/backups/first-lab
KEEP_DAYS=14
STAMP=$(date +%F)
mkdir -p "$DEST" && chmod 700 "$DEST"

# SQLite 는 쓰는 중에 그냥 복사하면 깨질 수 있어 백업 API 로 안전하게 복사
python3 - "$DEST/wordpress-$STAMP.sqlite" <<'PY'
import sqlite3, sys
src = sqlite3.connect("file:/var/www/first-lab/wp-content/database/.ht.sqlite?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
dst.close(); src.close()
PY
gzip -f "$DEST/wordpress-$STAMP.sqlite"
# 키워드 창고(황금 신호·서치콘솔 포함) — 주석과 달리 빠져 있었음 (11/5 검토)
python3 - "$DEST/keywords-$STAMP.sqlite" <<'PY' && gzip -f "$DEST/keywords-$STAMP.sqlite" || true
import sqlite3, sys
src = sqlite3.connect("file:/var/lib/first-lab/bot/keywords.db?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
dst.close()
PY
tar -czf "$DEST/uploads-$STAMP.tar.gz" -C /var/www/first-lab/wp-content uploads 2>/dev/null || true
tar -czf "$DEST/etc-$STAMP.tar.gz" -C /etc first-lab 2>/dev/null || true   # 키 파일 포함 → 폴더 권한 700
find "$DEST" -type f -mtime +$KEEP_DAYS -delete
chmod 600 "$DEST"/*
echo "백업 완료: $(du -sh "$DEST" | cut -f1) ($DEST)"
