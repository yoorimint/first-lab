# 매일 글쓰기 (예약 세션용 작업 지시서)

매일 아침 예약 작업으로 시작되는 Claude Code 세션이 이 문서대로 글 1편을 써서 사이트에 올린다.
이 문서를 고치면 다음 날부터 바로 반영된다 (예약 작업은 "이 문서를 따르라"고만 한다).

실패하면 아무것도 올리지 말고 끝낸다. 11:00에 서버 봇(API)이 대신 1편을 쓴다 (`bot/run.py`의 예비 동작).

## 0. 준비
- 저장소 루트에서 작업한다. 최신 main 을 받는다: `git fetch origin main && git checkout -B daily-writer origin/main`
- 환경 변수 `FIRSTLAB_API_TOKEN` 이 없으면 글을 쓰지 말고 "토큰 없음"으로 끝낸다.
- 스크래치 폴더(작업 파일)는 저장소 밖에 둔다.

## 1. 글감 고르기
```
curl -s -H "X-Firstlab-Token: $FIRSTLAB_API_TOKEN" https://first-lab.kr/wp-json/firstlab/v1/next
```
- `candidates` 는 점수순 후보다. 위에서부터 보고 아래에 해당하면 건너뛴다:
  - 날마다 값이 바뀌는 시세·가격(예: 오늘의 금 시세, 농산물 가격) — 글이 금방 틀린 정보가 됨
  - 특정 회사·브랜드 상품 비교, 추천·순위·이벤트, 의료·건강 진단, 정치·종교
  - `recent_posts` 에 이미 같은 주제(앞뒤 수식어만 다른 것)가 있는 키워드
- 처음으로 통과한 후보 1개를 고른다. 후보 키워드(`keyword`)는 띄어쓰기 없는 원문 그대로 쓴다.

## 2. 쓰기 규칙 불러오기
봇과 같은 작가·검수 규칙을 쓴다 (저장소의 코드가 기준):
```
python3 - <<'EOF'
from bot.writer import SYSTEM_PROMPT, build_prompt
from bot.factcheck import SYSTEM as REVIEW
# keyword, persona, target_length, extra_prompt 는 1단계 응답 값으로 채운다
print(SYSTEM_PROMPT); print(build_prompt(KEYWORD, TARGET_LENGTH, EXTRA_PROMPT, persona=PERSONA)); print(REVIEW)
EOF
```
작가 규칙(`SYSTEM_PROMPT` + `build_prompt`)대로 쓰고, 검수 규칙(`REVIEW`)으로 스스로 점검한다.

## 3. 조사
- WebSearch·WebFetch 합쳐 10번 안팎. 공식 출처(정부·공공기관·해당 기관 공식 사이트) 먼저.
- 공식 값과 블로그 값이 다르면 공식 값을 쓴다. 확인 못 한 것은 단정하지 않거나 뺀다.
- 핵심 수치마다 근거 주소를 메모해 둔다 (게시하지 않음).

## 4. 작성
- 출력 형식은 작가 규칙의 JSON 그대로 (title, excerpt, content_html, tags, category, images 3개).
- 계산 예시는 Python 으로 계산해서 그 결과를 그대로 쓴다. 표의 합계는 항목 합과 같아야 한다.
- 다 쓰면 숫자 일치를 확인한다 (False 면 고친다):
```
python3 -c "import json;from bot.writer import parse_article,align_with_body;a=parse_article(open('ARTICLE.json').read());print(align_with_body(a))"
```
  출력이 `[]` 이어야 한다.

## 5. 올리기
- `wordpress/queue/<영문-슬러그>.json` 으로 저장한다. 4단계 JSON 에 `"keyword": "<후보 원문>"`, `"status": "auto"` 를 더한다
  (auto = 설정 페이지의 발행 방식을 따름).
- 검사: `python3 -m unittest discover -s tests`
- 커밋하고 main 에 올린다 (운영자 승인된 자동 작업): 
  `git add wordpress/queue && git commit -m "매일 글: <키워드>" && git push origin HEAD:main`
- 배포 결과 확인 (예약 세션엔 GitHub 도구가 없으니 공개 API 사용, 2~5분 걸림):
  `curl -s "https://api.github.com/repos/yoorimint/first-lab/actions/runs?head_sha=$(git rev-parse HEAD)" | python3 -c "import json,sys;[print(r['name'],r['status'],r['conclusion']) for r in json.load(sys.stdin)['workflow_runs']]"`
  conclusion 이 success 면 끝. failure 면 원인만 보고하고 끝낸다.
- push 가 권한 문제로 실패하면 다른 방법을 찾지 말고 "push 실패"로 보고하고 끝낸다.

## 6. 마무리 보고 (세션 마지막 메시지)
- 고른 키워드와 건너뛴 후보(이유), 제목, 확인 못 한 주장, 배포 결과를 짧게 적는다.
