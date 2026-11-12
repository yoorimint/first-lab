# 매일 점검 (점검 모드 작업 지시서)

서버가 매일 '매일 점검 시각'에 예약 작업을 `maintenance-check` 로 실행하면(API 봇 방식이면 서버 안 같은 작업 — 보고 전문은 봇 기록, 문제가 있으면 GitHub 'Health check' 메일로 알림), 글을 쓰지 않고 이 문서대로 한다.
글 검수는 글을 쓸 때 끝난다 (daily-writer.md 4-1). 여기서는 **서버 기록만 점검해 운영자에게 알린다.** 파일은 바꾸지 않는다.

1. 상태 읽기:
   `curl -s -H "X-Firstlab-Token: $FIRSTLAB_API_TOKEN" https://first-lab.kr/wp-json/firstlab/v1/maintenance`
   (`log` 최근 기록, `continuous` 연속 작성 "1"/"0", `ready_count` 공개 대기 수, `held_drafts` 검수 보류 초안과 이유)
2. 점검: `log` 의 ❌(ok=false), '연속 작성 자동 정지', '실행 실패', '검수 미통과'를 찾아 원인을 한 줄씩 정리한다.
   같은 이유의 보류가 2번 이상 반복되면 관문 오탐일 수 있으니 이유를 그대로 적는다.
3. 품질: 저장소에서 `python3 automation/quality_report.py` — 최근 7일 외부 검수·공개 직전 재검수에서 글당 고친 곳 평균 한 줄 (⚠ 이면 문제 목록에도).
4. 보고 (세션 마지막 메시지 — 운영자 휴대폰 알림): **첫 줄은 `이상 없음` 또는 `문제 N건`** (API 봇은 이 줄로 알림을 보냄 — bot/api_writer.maint_problems), 그 아래 문제 목록, 품질 한 줄, 연속 작성 상태, 공개 대기 수, 보류 초안 수. 짧게.
