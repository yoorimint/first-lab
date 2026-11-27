#!/usr/bin/env bash
# 텔레그램 봇이 들어가 있는 채팅방 번호 보기 (상담 문의 알림 받을 방 고르기, bot/consult_notify.py).
# 먼저: set-secrets.sh FIRSTLAB_TELEGRAM_TOKEN 로 봇 토큰을 넣고, 단톡방에 봇을 초대한 뒤 그 방에서 /start 를 한 번 보낸다
# (그룹에선 봇이 명령(/로 시작)만 받는 설정이 기본이라 /start 가 필요).
# 나온 번호를 set-secrets.sh FIRSTLAB_TELEGRAM_CHAT 로 넣으면 끝. 토큰은 화면에 찍지 않는다.
set -euo pipefail
SECRETS=/etc/first-lab/secrets.env
TOKEN=$(grep -E '^FIRSTLAB_TELEGRAM_TOKEN=' "$SECRETS" 2>/dev/null | head -1 | cut -d= -f2- || true)
[ -n "$TOKEN" ] || { echo "토큰이 없어요. 먼저: $(dirname "$0")/set-secrets.sh FIRSTLAB_TELEGRAM_TOKEN"; exit 1; }

curl -s -m 15 "https://api.telegram.org/bot${TOKEN}/getUpdates" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit("텔레그램 응답을 읽지 못했어요. 잠시 뒤 다시 실행해 주세요.")
if not d.get("ok"):
    sys.exit("토큰이 맞지 않아요 (" + str(d.get("description", "")) + "). set-secrets.sh 로 다시 넣어 주세요.")
seen = {}
for u in d.get("result", []):
    for k in ("message", "my_chat_member", "channel_post", "edited_message"):
        c = (u.get(k) or {}).get("chat")
        if c:
            seen[c["id"]] = (c.get("type", ""), c.get("title") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x))
if not seen:
    sys.exit("아직 받은 메시지가 없어요. 봇을 단톡방에 초대하고 그 방에서 /start 를 보낸 뒤 다시 실행해 주세요.")
names = {"group": "단톡방", "supergroup": "단톡방", "private": "1:1 대화", "channel": "채널"}
print("채팅방 번호 (이 중 알림 받을 방의 번호를 FIRSTLAB_TELEGRAM_CHAT 에 넣으세요):")
for cid, (typ, title) in seen.items():
    print(f"  {cid}   {names.get(typ, typ)}  {title}")
'
