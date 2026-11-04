# 매일 글쓰기 (예약 세션용 작업 지시서)

매일 아침 예약 작업으로 시작되는 Claude Code 세션이 이 문서대로 새 글 1편을 써서 올리고, 갱신할 글이 있으면 1개 고친다.
이 문서를 고치면 다음 날부터 바로 반영된다 (예약 작업은 "이 문서를 따르라"고만 한다).

새 글이 실패하면 아무것도 올리지 말고 넘어간다. 11:00에 서버 봇(API)이 대신 1편을 쓴다 (`bot/run.py`의 예비 동작).
갱신은 예비가 없다 (실패하면 다음 날 서버가 다시 고름).

## 0. 준비
- 저장소 루트에서 작업한다. 최신 main 을 받는다: `git fetch origin main && git checkout -B daily-writer origin/main`
- 환경 변수 `FIRSTLAB_API_TOKEN` 이 없으면 글을 쓰지 말고 "토큰 없음"으로 끝낸다.
- 스크래치 폴더(작업 파일)는 저장소 밖에 둔다.

## 1. 글감 고르기
```
curl -s -H "X-Firstlab-Token: $FIRSTLAB_API_TOKEN" https://first-lab.kr/wp-json/firstlab/v1/next
```
- 응답의 `write_mode` 가 `"direct"` 가 아니면(설정 페이지에서 "API 봇" 선택) 글을 쓰지 말고 "봇 방식이라 쉼"으로 끝낸다.
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
- 내부 링크: 1단계 응답 `recent_posts` 중 `url` 이 있는(발행된) 글이 본문과 실제로 관련 있으면, 관련 문장 안에
  `<a href="<url>">자연스러운 문구</a>` 로 1~3개 건다. 관련 없으면 걸지 않는다. 목록에 없는 주소·외부 사이트 주소는 서버가 링크를 지운다.
- 다 쓰면 숫자 일치를 확인한다 (False 면 고친다):
```
python3 -c "import json;from bot.writer import parse_article,align_with_body;a=parse_article(open('ARTICLE.json').read());print(align_with_body(a))"
```
  출력이 `[]` 이어야 한다.

## 4-1. 계산기 (주제에 정해진 계산 공식이 있을 때만)
수수료표·세율·요율·법령 계산식처럼 **정해진 공식**이 있고, 독자가 자기 값을 넣어볼 이유가 있는 주제면 계산기를 본문에 넣는다.
공식이 없는 주제(신청 방법, 서류, 절차 안내)는 넣지 않는다.
- 계산기 폴더: `wordpress/themes/first-lab/calculators/` (구조 설명은 `calc.js` 머리말). 예시: `car-inspection`.
- 이미 있는 계산기(`defs/*.js`)로 되면 새로 만들지 말고 그걸 넣는다.
- 없으면 새로 만든다 (id 는 영문 소문자-하이픈):
  - `defs/<id>.js`: `car-inspection.js` 와 같은 형식 (입력 항목 + compute). 화면 문구는 본문과 같은 말투.
  - `data/<id>.json`: 요율·금액은 전부 여기에. `basis_date`(오늘), `sources`(조사한 공식 출처 이름·주소)
  - `tests/<id>.json`: 검증 사례 3개 이상. 기대값은 공식 기관 계산 예시나 법령 문구로 직접 확인한 값만 (계산기로 낸 값을 그대로 기대값으로 쓰지 않는다)
  - `wordpress/pages/<id>-calculator.html`: 단독 페이지 (첫 줄 `<!-- title: ○○ 계산기 -->`, 짧은 안내 + 계산기 + 결과 읽는 법)
  - 검사: `node tests/test_calculators.mjs` 가 통과해야 한다
- 요율·기준 중 하나라도 공식 출처로 확인 못 하면 계산기를 만들지 않는다 (글만 올린다).
- 본문에 넣는 법: `content_html` 의 계산 예시 근처에 **독립된 줄**로 `<p>[firstlab_calc id="<id>"]</p>`
- 계산기는 별도 커밋으로 먼저 올린다: `git add wordpress/themes/first-lab/calculators wordpress/pages && git commit -m "계산기: <이름>"`

## 5. 올리기
- `wordpress/queue/<영문-슬러그>.json` 으로 저장한다. 4단계 JSON 에 `"keyword": "<후보 원문>"`, `"status": "auto"` 를 더한다
  (auto = 설정 페이지의 발행 방식을 따름).
- 검사: `python3 -m unittest discover -s tests && node tests/test_calculators.mjs`
- 커밋하고 main 에 올린다 (운영자 승인된 자동 작업): 
  `git add wordpress/queue && git commit -m "매일 글: <키워드>" && git push origin HEAD:main`
- 배포 결과 확인 (예약 세션엔 GitHub 도구가 없으니 공개 API 사용, 2~5분 걸림):
  `curl -s "https://api.github.com/repos/yoorimint/first-lab/actions/runs?head_sha=$(git rev-parse HEAD)" | python3 -c "import json,sys;[print(r['name'],r['status'],r['conclusion']) for r in json.load(sys.stdin)['workflow_runs']]"`
  conclusion 이 success 면 끝. failure 면 원인만 보고하고 끝낸다.
- push 가 권한 문제로 실패하면 다른 방법을 찾지 말고 "push 실패"로 보고하고 끝낸다.

## 6. 글 갱신 (있는 날만)
1단계 응답의 `refresh_target` 이 `null` 이면 건너뛴다. 있으면 그 글 1개를 고친다 (새 글이 실패했어도 따로 진행).
- `mode` 가 `"enhance"`(보강): `bot.refresh.ENHANCE_SYSTEM` 규칙대로. `siblings` 는 이 글에서 함께 답할 검색어다.
  `why` 가 순위 정체·하락이면 상위 글들이 다루는데 이 글에 빠진 하위 질문을 채우는 게 핵심이다.
- `mode` 가 `"recheck"`(재검증): `bot.factcheck.SYSTEM` 규칙대로 수치·기준만 공식 출처로 다시 확인하고 바뀐 것만 고친다.
- 공통: `content_html` 의 구조·말투, 이미지(figure)·링크(a) 태그는 그대로 둔다. 본문을 줄이지 않는다 (기존의 80% 미만이면 서버가 거부).
  제목은 바꾸지 않는다. 조사는 3단계와 같은 원칙.
- `wordpress/queue/refresh-<id>-<오늘 날짜>.json` 으로 저장:
  `{"type": "refresh", "post_id": <id>, "base_modified": "<응답 값 그대로>", "content_html": "<고친 본문 전체>", "changes": "<무엇을 바꿨는지 한 줄>"}`
  (`base_modified` 는 그 사이 운영자가 글을 고쳤으면 덮어쓰지 않게 하는 값이다. 바꾸지 말 것)
- 고칠 게 없으면 파일을 만들지 않는다.
- 5단계와 같이 검사 → 커밋(`매일 갱신: <글 제목>`) → main 에 올림 → 배포 확인.

## 7. 마무리 보고 (세션 마지막 메시지)
- 새 글: 고른 키워드와 건너뛴 후보(이유), 제목, 확인 못 한 주장, 배포 결과.
- 갱신: 대상 글, 바꾼 내용, 배포 결과 (없으면 "갱신 대상 없음").
