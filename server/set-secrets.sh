#!/usr/bin/env bash
# 서버 비밀값 입력/수정 도구. 입력 내용은 화면과 명령 기록에 남지 않는다.
#   사용: bash /opt/first-lab/server/set-secrets.sh 키이름1 [키이름2 ...]
#   예)   bash /opt/first-lab/server/set-secrets.sh NAVER_AD_CUSTOMER_ID NAVER_AD_ACCESS_LICENSE NAVER_AD_SECRET_KEY
set -euo pipefail
SECRETS=/etc/first-lab/secrets.env
[ $# -ge 1 ] || { echo "사용법: $0 키이름 [키이름 ...]"; exit 1; }

install -d -m 700 /etc/first-lab
touch "$SECRETS" && chmod 600 "$SECRETS"

for name in "$@"; do
  read -rsp "$name 값 붙여넣기 후 Enter: " value; echo
  value=$(printf '%s' "$value" | tr -d '[:space:]')   # 복사 시 섞인 공백 제거
  [ -n "$value" ] || { echo "  → 빈 값이라 건너뜀"; continue; }
  grep -v "^$name=" "$SECRETS" > "$SECRETS.tmp" || true
  echo "$name=$value" >> "$SECRETS.tmp"
  mv "$SECRETS.tmp" "$SECRETS" && chmod 600 "$SECRETS"
  echo "  → 저장됨 (${#value}자)"
done
echo "완료. 저장된 키 이름: $(cut -d= -f1 "$SECRETS" | tr '\n' ' ')"
