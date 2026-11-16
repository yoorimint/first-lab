#!/usr/bin/env bash
# 전체 배포가 되는 변경은 새벽(02~06시 KST)에만 main 에 — check_and_push.sh 가 부름.
# 11/17 감시 세션이 server/ 변경을 08:22 에 올려 낮에 전체 배포(부하 경보 원인)가 돎 — 취소 권한도 없었음.
# 전체 배포 조건은 .github/workflows/deploy.yml 과 같게. 급하면 FULL_DEPLOY_OK=1.
# 사용: <바뀐 파일 목록> | full_deploy_guard.sh [시(00~23, 기본 지금 KST)]  → 막으면 종료코드 1
hour=${1:-$(TZ=Asia/Seoul date +%H)}
full=$(grep -E '^(server/|\.github/|bot/requirements\.txt|wordpress/pages/)' | tr '\n' ' ')
if [ -n "$full" ] && { [ "$((10#$hour))" -lt 2 ] || [ "$((10#$hour))" -ge 7 ]; } && [ "${FULL_DEPLOY_OK:-}" != 1 ]; then
  echo "!! 전체 배포가 되는 변경(${full% }) — 새벽 2~6시에만 main 에 올림 (커밋은 작업 브랜치에만). 급하면 FULL_DEPLOY_OK=1"
  exit 1
fi
exit 0
