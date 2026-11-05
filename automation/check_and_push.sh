#!/usr/bin/env bash
# 감시 세션이 main 에 올릴 때 쓰는 명령: 검사가 하나라도 실패하면 올리지 않는다.
# (11/5 감시 세션이 '… | tail -1 && git push' 로 써서 테스트 실패(FAILED)인데도 올라간 일 — tail 은 늘 성공함)
# 사용: bash automation/check_and_push.sh "커밋 메시지"
set -euo pipefail
cd "$(dirname "$0")/.."
msg="${1:?커밋 메시지}"
id ci >/dev/null 2>&1 || useradd -M ci
run_tests() {
  runuser -u ci -- env HOME=/tmp python3 -B -m unittest discover -s tests >/tmp/check_and_push.log 2>&1 \
    || { tail -30 /tmp/check_and_push.log; echo "!! 테스트 실패 — 올리지 않음"; exit 1; }
  node tests/test_calculators.mjs >/dev/null || { echo "!! 계산기 검사 실패 — 올리지 않음"; exit 1; }
}
run_tests
git add -A
git diff --cached --quiet && { echo "바뀐 것 없음"; exit 0; }
git commit -q -m "$msg"
for wait in 2 4 8 16; do
  git fetch -q origin main && git rebase -q origin/main && run_tests && git push -q origin HEAD:main && break
  sleep "$wait"
done
branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" != "main" ] && git push -q -f origin "HEAD:$branch" || true
git log --oneline -1 | cut -c1-80
