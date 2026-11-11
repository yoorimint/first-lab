#!/usr/bin/env bash
# 감시 세션이 main 에 올릴 때 쓰는 명령: 검사가 하나라도 실패하면 올리지 않는다.
# (11/5 감시 세션이 '… | tail -1 && git push' 로 써서 테스트 실패(FAILED)인데도 올라간 일 — tail 은 늘 성공함)
# 사용: bash automation/check_and_push.sh "커밋 메시지"
set -euo pipefail
cd "$(dirname "$0")/.."
msg="${1:?커밋 메시지}"
# 패치 목록(JSON)은 양쪽 항목 합치기로 병합 (.gitattributes merge=jsonlist, 11/6 감시 세션·글쓰기 작업 동시 추가 충돌)
git config merge.jsonlist.driver "python3 automation/merge_json_list.py %O %A %B"
git config merge.ours.driver true   # .gitattributes merge=ours (생성 파일 — 아래 regen 이 다시 만듦)
id ci >/dev/null 2>&1 || useradd -M ci
run_tests() {
  runuser -u ci -- env HOME=/tmp python3 -B -m unittest discover -s tests >/tmp/check_and_push.log 2>&1 \
    || { tail -30 /tmp/check_and_push.log; echo "!! 테스트 실패 — 올리지 않음"; exit 1; }
  node tests/test_calculators.mjs >/dev/null || { echo "!! 계산기 검사 실패 — 올리지 않음"; exit 1; }
}
# 자주 틀린 유형표는 patches.json·quality 기록에서 만드는 파일 — 병합(union)하면 줄이 섞여 테스트가 깨짐 (11/7) → 늘 다시 만듦
regen() { python3 automation/common_errors.py >/dev/null; }
regen
run_tests
git add -A
# 이미 올린 갱신 파일(refresh-*.json)은 고치지 않는다 — 서버가 첫 판을 반영한 뒤라 고친 판은 '그 사이 수정됨'으로 거절되고
# 틀린 첫 판이 남음 (11/11 #1143 '아니요' 답). 고칠 것은 새 패치(apply_review.py)나 새 갱신 파일로.
edited=$(git diff --cached --name-status | awk '$1=="M" && $2 ~ /^wordpress\/queue\/refresh-.*\.json$/ {print $2}')
[ -z "$edited" ] || { echo "!! 이미 올린 갱신 파일을 고침 — 올리지 않음: $edited (apply_review.py 패치로 고치기)"; git reset -q; exit 1; }
git diff --cached --quiet && { echo "바뀐 것 없음"; exit 0; }
git commit -q -m "$msg"
pushed=""
for wait in 2 4 8 16; do
  git fetch -q origin main && { git rebase -q origin/main || { git rebase --abort 2>/dev/null; false; }; } \
    && { regen; git diff --quiet automation/common-errors.md || git commit -q -m "자주 틀린 유형표 다시 만듦 (병합 뒤)" automation/common-errors.md; } \
    && run_tests && git push -q origin HEAD:main && { pushed=1; break; }
  sleep "$wait"
done
# 네 번 다 실패하면 성공처럼 끝내지 않음 (11/6 GitHub 'commit_refs_rules' 거부가 4번 반복됐는데 커밋 줄을 찍고 끝나 올라간 줄 앎)
[ -n "$pushed" ] || { echo "!! main 에 올리기 실패 (커밋은 이 컴퓨터에만 있음) — 다음 차례에 git push origin HEAD:main 다시"; exit 1; }
branch=$(git rev-parse --abbrev-ref HEAD)
# 작업 브랜치도 같은 커밋으로 (main 위에 다시 쌓았으니 일반 push 로 충분 — 11/6 '-f' 가 권한에 막히고 '|| true' 가 숨겨 브랜치가 계속 뒤처짐)
if [ "$branch" != "main" ]; then
  git push -q origin "HEAD:$branch" || echo "!! 작업 브랜치 $branch 올리기 실패 (main 은 올라감) — git status 로 확인"
fi
git log --oneline -1 | cut -c1-80
