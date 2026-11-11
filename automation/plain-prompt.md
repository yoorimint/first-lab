# 쉬운 말 다듬기 지시문 (감시 세션이 서브에이전트에게 줌, model: opus) — 11/9 운영자 '기존 글 전체 쉬운 말로'

`{N}` 자리에 글 번호를 넣어 그대로 준다.
순서 (11/10 — 19편 다듬기에서 조건·단서 빠짐·뜻풀이 오류 50곳 넘게 나옴): ① 이 지시문으로 다듬기(서브에이전트 A) → ② 다른 서브에이전트가 원래 글과 문장마다 뜻 비교·법 정의 대조 후 고치기 (지시문: 이 파일 끝 '뜻 검수') → ③ 감시 세션이 `python3 automation/plain_diff.py <원본 JSON> <고친 HTML>` 로 바뀐 곳을 **처음부터 끝까지 직접 읽고** 틀린 곳을 고친 뒤에만 plain_check --write. 출력 맨 위 [점수표]가 '미달'이거나 읽어 보고 90점(기준 글 #1419 수준)이 안 되면 반영하지 말고 다시 다듬는다 (운영자 11/10 '최종 검토할 때 점수도 확인해서 미달이면 다시 수정해야지') — 다듬은 쪽 자체 점수는 믿지 않는다 (운영자 11/10 '수정한 거 저장하기 전에 니가 봐야지, 공개 전에 보면 늦지 — 초안 저장은 완벽한 상태에서'). ②를 건너뛰고 반영하지 않는다.

뜻 검수 지시 (②): ORIGINAL=/tmp/firstlab-review/post-{N}.json content_html, REWRITE=/tmp/firstlab-review/plain-{N}.html. 문장마다 비교해 조건이 빠지거나 넓어지거나 좁아진 곳, 단서(원칙적으로·보통·이상/초과/미만·상시 ○명)가 바뀐 곳, 해석을 덧붙인 곳, 상자·확인 목록이 과장한 곳, 틀린 뜻풀이(법 정의가 있으면 python3 -m bot.lawtext 로 대조)를 원문 → 다듬은 글 → 고친 글로 적고, 고친 본문을 plain-{N}-r.html 로 쓰고 plain_check 통과까지.

---
You are rewriting ONE existing Korean info blog post into plain, easy Korean — same facts, easier words.
Input: /tmp/firstlab-review/post-{N}.json (field content_html; also title/excerpt for context). Do NOT edit any repo files.
Write the full rewritten content_html to /tmp/firstlab-review/plain-{N}.html.

Target reader: a Korean middle-school first grader (중학교 1학년). Every sentence should be understandable to them on first read.

Goal (운영자 11/9): someone who knows NOTHING about law or jargon reads it smoothly, top to bottom, and thinks "아, 그렇구나". Write like a kind friend explaining over coffee: what it is → why it matters to me → how much / what to do. Readability matters: short paragraphs (2–3 sentences), lists for steps or conditions.

Standard (운영자 11/10 '니가 봤을 때 최소 90점 이상 — 이 수준 기억해라, 우리 컨셉'): /home/user/first-lab/automation/plain-example-1419.html (주휴수당, 93점+) is THE reference. Read it first and match its shape and level:
- First <p> = "한눈에 보기" box, split into lines with <br> (phone ≈ 18 Korean chars per line): line 1 = THE ANSWER in plain words, containing the main search term (Google takes the first sentence for its summary — 운영자 11/10 '답, 도입 순서'); line 2 = a short friendly intro/hook (who it is for, why it matters; no unverified claims like '의외로 많아요'); then 2–4 '✔ ' lines (each ≤ 22 chars: key conditions / key amount — a ✔ line must not drop the condition it depends on), optionally a short '(… 기준)' line ≤ 20 chars.
- Do NOT repeat the box below it (운영자 11/10 '반복 줄여라'): paragraphs between the box and the first h2 add only what the box does not say (the check fails if 3+ box numbers reappear there). Keep the '이런 건 꼭 확인하세요' list to things worth re-checking, not a copy of the box.
- Every <p> has at most 2 sentences, grouped by meaning (the check fails otherwise). Example first, rule after.
- Level: easy words, ~해요 tone, but adults are reading — not childish. Terms stay (searched), explained once.
- Before the FAQ h2 (or at the end of the last body section if there is no FAQ): <p><strong>이런 건 꼭 확인하세요</strong></p> + <ul> with 3–5 items summarizing facts ALREADY in the post (no new facts), then <p><strong>한 줄 정리:</strong> …</p> (one sentence with the main search term). These render as a box and a blue band.
- Table header cells short (≤ 10 chars); move a long qualifier (e.g. '2026년, 시급 10,320원 기준') into a sentence right above the table.
- Rare cases: shortest possible, never expanded.
- Self-score before reporting (0–100, as a strict editor reading on a phone): if under 90, fix and re-check. Report the score and what still costs points.

Rules
00. Parentheses: at most ONE per sentence, and only for a key search term (e.g. 소정근로시간) — never stack '(유급휴일)…(근로기준법)…(소정근로일)'. Do NOT put law names/articles inside body sentences; instead end each section (before the next h2) with one short line: <p class="fl-basis">근거: 근로기준법 제55조, 같은 법 시행령 제30조</p> — keep every law/article of that section there (the check script counts them).
000. Hard condition sentences: show a tiny example first ("예를 들어 …면 받아요 / …면 못 받아요") using ONLY numbers in the original, then the rule in one short sentence, then why (only if the original says why).
0. Plain words: say what a thing IS before its legal name. '사용자' may become '회사' (gloss once: '회사(사용자)'). Do not repeat the same gloss — after the first time, use the term alone. One idea per sentence; average sentence ≤ 40 characters (the check script measures it). Before a hard rule, show a tiny example using ONLY numbers already in the original.
1. Facts must not change. Keep EVERY number, amount, rate, date, period, law article (제N조…), link (<a href>) and shortcode ([firstlab_calc …]) exactly; keep every condition and exception (다만 …). Do not add new facts or numbers. Do not delete sentences that carry a condition.
2. Keep the HTML structure: same h2/h3 headings (you may reword heading text slightly but keep the count), same tables (same rows; you may simplify cell wording but keep numbers), same lists. Keep the "공식 안내 바로가기" section as is.
3. First paragraph: start with ONE plain sentence that answers the reader's question like you would to a friend — max 70 characters, no parentheses, no law article, no jargon. Then the original first-paragraph content (made easier).
4. Jargon: the first time a technical term appears (e.g. 소정근로일, 통상임금, 과세표준, 원천징수), explain it once in everyday words, e.g. "소정근로시간은 계약으로 정한 일하는 시간이에요". KEEP USING THE TERM ITSELF afterwards — people search these words (운영자 11/9 '검색에도 걸려야 되니 기존 단어 다 없애면 안 된다'). The check script fails if a title/heading word or a term used 3+ times in the original drops below half its original count.
4-0. Term meanings ARE allowed (운영자 11/9 '용어 뜻풀이는 해야지'): explain a technical term in everyday words using its legal definition — check the definition first with `python3 -m bot.lawtext <법령명> <제N조>` (e.g. 근로기준법 제2조, 시행령 제6조 통상임금, 근로기준법 제63조) and say in the report which article you used. Still NO new facts, numbers, cases or example jobs that the original or the definition article does not contain.
4-0-1. Headings and table header/left cells: keep the official search words people type (주휴수당, 소정근로시간 …) but say them so anyone understands (e.g. '근로기준법상 근로자' → '일하는 사람(근로자)'). Do not define the same thing twice in a row. Shortening a header must not drop a condition ('소득·재산 없음' → '무소득' drops 재산, #254).
4-1. Do NOT reword legal threshold words that change meaning when simplified — keep '상시 ○명', '소정', '이상·이하·초과·미만', '원칙적으로' as they are (you may add a plain explanation next to them). Do NOT add examples that are not in the original (e.g. '경비처럼'). (11/9 시범 #1419: '상시 5명' → '늘 5명'으로 뜻이 바뀜)
4-1-1. Legal minimums: write them as the law does — 연장·야간·휴일 가산은 '통상임금의 50% 이상 (8시간 넘는 휴일근로는 100% 이상)', 해고예고수당 '30일분 이상' (11/10: 대기 글 여러 편이 '50% 가산'으로 '이상'을 빠뜨림 — 다듬을 때 근로기준법 제56조대로 '이상'을 붙인다; 숫자는 그대로라 검사 통과).
4-1-2. Known original errors found while rewriting (fix them when you see them, verified): 장기요양보험 13.14%(소득 대비 0.9448%)는 '2026년 1월분부터' 적용 — '2026년 11월분부터'는 틀림 (노인장기요양보험법 시행령 제4조, 2025.12.30 개정). 11월분부터 바뀌는 건 비율(13.14%)을 반올림해 쓰는 것뿐 (법 제9조①, 2026-11-27 시행). Number changes for verified fixes need `--fix=<숫자들>:<근거>` on plain_check (the 감시 세션 does this).
4-1-2-1. '근로감독관' becomes '노동감독관' on 2026-12-08 (근로기준법 제101조 개정, verified): at the first mention write '근로감독관(2026년 12월 8일부터 노동감독관)' (needs --fix=12,8 — tell the 감시 세션). After that date the checks block '근로감독관' alone (condition_gate.DATED_WRONG).
4-1-3. A deadline or schedule date that is already past today must be written as past ('…까지였어요, 이미 지났어요'), never as a future step (11/10 #561 '이 기한은 2026년 11월 9일까지예요' — plain_check 지난 기한 검사).
4-2. Rare cases (exceptions that apply to few readers): keep every condition, but say each in one or two short sentences — do not expand them with long explanations. Do not explain the same idea in the lead and again in the next section.
5. Short sentences: max 100 characters per sentence, paragraph ≤ 300, list item ≤ 200. Keep the friendly ~해요 tone. No jokes, no invented anecdotes.
6. Check: run `cd /home/user/first-lab && python3 automation/plain_check.py {N} /tmp/firstlab-review/post-{N}.json /tmp/firstlab-review/plain-{N}.html` and fix until it prints "통과". (Repeating an existing number in the first sentence is allowed.) If it says "요약문:", write a new excerpt (≤120자, numbers only from the body) to /tmp/firstlab-review/excerpt-{N}.txt and re-run with `--excerpt=/tmp/firstlab-review/excerpt-{N}.txt`.
Report in Korean: the new first sentence, 3–5 examples of before→after (jargon/sentences), and anything you were unsure about. Do not write the --write file.
