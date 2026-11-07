// 계산기 배포 전 검사 (GitHub Actions). 실행: node tests/test_calculators.mjs
// 계산기마다 defs/<id>.js + data/<id>.json(기준일·출처) + tests/<id>.json(사례 3개 이상)이 있어야 하고,
// 사례의 expect 값이 compute() 의 values 와 모두 같아야 한다. 하나라도 틀리면 배포가 막힌다.
// 기준선 사례("boundary": true)가 2개 이상 있어야 한다: 공제액·과락 점수·면세점·구간 경계·상한/하한에 딱 맞는 값과
// 바로 아래/위 값 (11/5 새 계산기 2개 모두 기준선에서 틀림: 평균 59.996 을 60 으로 올려 합격, 공동명의 비율).
// 잘못된 입력 사례는 "expect_error": true — compute() 가 InputError(화면 안내 문장)를 내야 통과.
// 안내 문장은 "notes_include"/"notes_exclude": [문구] — 그 사례의 notes 에 있어야/없어야 통과.
// 연초 값: data 에 "yearly": false 또는 "yearly_checked": <연도> 필수 (bot/calc_watch.py 가 다음 해 확인 안 된 계산기를 글쓰기 세션에 넘김).
// 미리 넣은 다음 값("scheduled")은 키가 data 에 있어야 하고, 출처와 그 날짜 사례("today": "YYYY-MM-DD")가 있어야 한다.
// scheduled 의 "texts": [[옛 표기, 새 표기]] — 계산기 페이지에 옛 표기가 있으면 새 표기도 있어야 함 (글은 bot/calc_watch.py 가 그날 재확인 표시).
// data.links(결과 행의 관련 글 주소)는 대기열 글 키워드(wordpress/queue/*.json)나 고정 페이지(wordpress/pages/*.html) 주소여야 한다.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIR = path.join(ROOT, 'wordpress/themes/first-lab/calculators');
const MIN_CASES = 3;
const MIN_BOUNDARY = 2;

const context = vm.createContext({ console });
const load = (file) => vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
load(path.join(DIR, 'calc.js'));
const { defs, util } = context.FirstLabCalc;

const failures = [];
const fail = (id, msg) => failures.push(`[${id}] ${msg}`);
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

const ids = fs.readdirSync(path.join(DIR, 'defs')).filter((f) => f.endsWith('.js')).map((f) => f.slice(0, -3));

// 우리 사이트 글·페이지 주소 (/%postname%/ — 글 주소 = 키워드, publish_queue.py)
const QUEUE = path.join(ROOT, 'wordpress/queue');
// 10원 끝수 처리를 아직 확인 못 한 세금 계산기 (11/7 발견) — 이 날짜까지 확인해 won10_keys 또는 won10 이유를 넣기
const WON10_PENDING = { until: '2026-11-14', ids: ['capital-gains-tax', 'income-tax', 'property-holding-tax'] };
const PAGES = path.join(ROOT, 'wordpress/pages');
const sitePaths = new Set([
  ...fs.readdirSync(QUEUE).filter((f) => f.endsWith('.json')).map((f) => readJson(path.join(QUEUE, f)).keyword).filter(Boolean),
  ...fs.readdirSync(PAGES).filter((f) => f.endsWith('.html')).map((f) => f.slice(0, -5)),
].map((slug) => `/${String(slug).trim()}/`));
let checked = 0;

// 버림 보정 자체 (11/6 #1019): 오차만 메우고 진짜 끝수는 올리지 않아야 함
for (const [n, want, why] of [
  [(3800000 / 209) * 11 * 1.5, 300000, '소수 오차 299,999.99… → 300,000 (11/5)'],
  [999999999999 / 2 / 1000, 499999999, '큰 금액 끝수 .9995 는 버림 (11/6 중간예납 1조 원 근처)'],
  [1e12 / 1000 - 0.001, 999999999, '1조 원 규모 끝수 .999'],
  [0.1 + 0.2, 0, '작은 값'],
]) {
  if (util.floor(n) !== want) fail('util.floor', `${why}: ${n} → ${util.floor(n)} (기대 ${want})`);
  if (util.trunc(-n) !== -want) fail('util.trunc', `${why} (음수): ${-n} → ${util.trunc(-n)} (기대 ${-want})`);
}

for (const id of ids) {
  load(path.join(DIR, 'defs', `${id}.js`));
  const def = defs[id];
  if (!def) { fail(id, `defs/${id}.js 가 id '${id}' 로 register 하지 않음`); continue; }

  // 소수 오차 버림 금지: Math.floor/Math.trunc 대신 util.floor/trunc (3,800,000÷209×11×1.5 = 299,999.99… → 1원 빠짐, 11/5 두 번)
  const src = fs.readFileSync(path.join(DIR, 'defs', `${id}.js`), 'utf8');
  if (/Math\.(floor|trunc)\(/.test(src)) fail(id, 'Math.floor/Math.trunc 대신 FirstLabCalc.util.floor/trunc 를 써야 함 (소수 오차로 1원 빠짐)');

  let data;
  let cases;
  try {
    data = readJson(path.join(DIR, 'data', `${id}.json`));
    cases = readJson(path.join(DIR, 'tests', `${id}.json`));
  } catch (e) {
    fail(id, `data/tests 파일 없음 또는 JSON 오류: ${e.message}`);
    continue;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(data.basis_date || '')) fail(id, 'data.basis_date 없음 (YYYY-MM-DD)');
  if (!(data.sources || []).length || data.sources.some((s) => !s.name || !/^https:\/\//.test(s.url || ''))) {
    fail(id, 'data.sources 에 이름과 https 주소가 있는 출처가 필요함');
  }
  if (data.yearly !== false && !Number.isInteger(data.yearly_checked)) {
    fail(id, 'data 에 "yearly_checked": <확인한 연도> (해마다 바뀌는 값이 없으면 "yearly": false) 가 필요함 — 연초 값 감시 (bot/calc_watch.py)');
  }
  for (const [n, sch] of (data.scheduled || []).entries()) {
    const where = `data.scheduled[${n}]`;
    if (!util.parseDate(sch.from) || sch.from <= data.basis_date) fail(id, `${where}.from 이 기준일 뒤의 날짜(YYYY-MM-DD)가 아님`);
    for (const key of Object.keys(sch.values || {})) if (!(key in data)) fail(id, `${where}.values.${key}: data 에 없는 키 (오타면 새 값이 안 쓰임)`);
    if (!Object.keys(sch.values || {}).length) fail(id, `${where}.values 가 비어 있음`);
    if (!sch.source || !sch.source.name || !/^https:\/\//.test(sch.source.url || '')) fail(id, `${where}.source 에 이름과 https 주소가 필요함`);
    if (!(cases || []).some((c) => (c.today || '') >= sch.from)) fail(id, `${where}: ${sch.from} 부터의 사례("today": "${sch.from}")가 없음`);
    // 계산기 단독 페이지가 옛 값만 적고 있으면 그날부터 틀림 (11/5: 4.75%·10,320원이 페이지에 박혀 있었음) → 새 값도 같이 적어야 함
    const pageFile = path.join(PAGES, `${id}-calculator.html`);
    const page = fs.existsSync(pageFile) ? fs.readFileSync(pageFile, 'utf8') : '';
    for (const [oldText, newText] of sch.texts || []) {
      if (page.includes(oldText) && !page.includes(newText)) fail(id, `${where}: 페이지에 옛 값 '${oldText}' 만 있음 — '${newText}'(${sch.from}부터)도 적기`);
    }
  }
  // 세금 계산기는 낼 세액의 10원 미만 끝수 처리를 밝혀야 함 (11/7 #1754 상속세: won10_keys 가 없어 1원 단위로 보여 줌)
  // won10_keys(버리는 값) 또는 "won10": "<10원 버림을 안 하는 이유>" 중 하나. 아직 확인 못 한 계산기는 WON10_PENDING (기한 지나면 실패)
  if (/-tax$/.test(id) && !(data.won10_keys || []).length && typeof data.won10 !== 'string') {
    if (!(WON10_PENDING.ids.includes(id) && new Date().toISOString().slice(0, 10) <= WON10_PENDING.until)) {
      fail(id, 'data 에 "won10_keys": [낼 세액 키] 또는 "won10": "<끝수 처리 안 하는 이유>" 가 필요함 (국고금관리법 제47조①)');
    }
  }
  for (const [n, h] of (data.history || []).entries()) {
    const where = `data.history[${n}]`;
    if (!util.parseDate(h.until) || h.until >= data.basis_date) fail(id, `${where}.until 이 기준일 앞의 날짜(YYYY-MM-DD)가 아님`);
    for (const key of Object.keys(h.values || {})) if (!(key in data)) fail(id, `${where}.values.${key}: data 에 없는 키 (오타면 옛 값이 안 쓰임)`);
    if (!Object.keys(h.values || {}).length) fail(id, `${where}.values 가 비어 있음`);
    if (!h.source || !h.source.name || !/^https:\/\//.test(h.source.url || '')) fail(id, `${where}.source 에 이름과 https 주소가 필요함`);
    if (!(cases || []).some((c) => Object.values(c.input || {}).some((x) => typeof x === 'string' && util.parseDate(x) && x <= h.until))) {
      fail(id, `${where}: ${h.until} 이전 날짜를 넣은 사례가 없음 (옛 값이 실제로 쓰이는지 확인)`);
    }
  }
  if (!Array.isArray(cases) || cases.length < MIN_CASES) fail(id, `검증 사례가 ${MIN_CASES}개 이상 필요함`);
  else if (cases.filter((c) => c.boundary === true).length < MIN_BOUNDARY) {
    fail(id, `기준선 사례("boundary": true)가 ${MIN_BOUNDARY}개 이상 필요함 — 공제액·합격 점수·면세점·세율 구간·상한/하한에 딱 맞는 값과 바로 아래/위 값을, 계산기 말고 법령대로 손으로 계산해 넣기`);
  }

  // 사례에 소수를 넣는 칸은 소수점 있는 자판이어야 함 (step 이 정수면 휴대폰 숫자 자판에 소수점이 없어 평단가 9,333.33 을 못 넣음, 11/5)
  const inputsByName = Object.fromEntries(def.inputs.map((input) => [input.name, input]));
  for (const c of (cases || []).filter((x) => x.expect_error !== true)) { // 잘못된 입력 사례(소수 나이 등)는 제외
    for (const [name, value] of Object.entries(c.input || {})) {
      const input = inputsByName[name];
      if (input && input.type === 'number' && typeof value === 'number' && !Number.isInteger(value) && util.inputMode(input) !== 'decimal') {
        fail(id, `'${name}' 칸에 소수(${value})를 넣는 사례가 있는데 step 이 정수라 소수점 없는 자판이 뜸 — step 을 0.01 처럼 소수로`);
      }
    }
  }

  for (const [key, href] of Object.entries(data.links || {})) {
    if (!sitePaths.has(href)) fail(id, `data.links.${key} = ${href}: 대기열 글 키워드나 고정 페이지 주소가 아님`);
  }

  for (const [i, c] of (cases || []).entries()) {
    let out;
    const day = c.today || data.basis_date; // 사례 날짜 (없으면 기준일) — 그날 적용되는 값으로 계산 (calc.js util.dataOn)
    const todayIso = util.todayIso;
    util.todayIso = () => day;
    try {
      out = def.compute(c.input, util.dataOn(data, day), util);
    } catch (e) {
      if (c.expect_error === true && e instanceof util.InputError) { checked += 1; continue; }
      fail(id, `사례 ${i + 1} 계산 오류: ${e.message}`);
      continue;
    } finally {
      util.todayIso = todayIso;
    }
    if (c.expect_error === true) { fail(id, `사례 ${i + 1} (${c.why}): 잘못된 입력인데 안내 문장(InputError) 없이 계산됨`); continue; }
    // 세금·보험료 징수액은 10원 미만 끝수를 버림 (국고금관리법 제47조①) — data.won10_keys 에 적은 값은 10원 단위여야 함
    // (11/5 실수령액·연말정산 계산기 둘 다 1원 단위로 계산해 틀림)
    for (const key of data.won10_keys || []) {
      if (typeof out.values[key] === 'number' && out.values[key] % 10 !== 0) fail(id, `사례 ${i + 1}: ${key} = ${out.values[key]} — 10원 미만 끝수를 버려야 함`);
    }
    for (const [key, want] of Object.entries(c.expect || {})) {
      const got = out.values[key];
      if (got !== want) fail(id, `사례 ${i + 1} (${c.why}): ${key} = ${JSON.stringify(got)}, 기대값 ${JSON.stringify(want)}`);
    }
    // 안내 문장도 사례로 확인 (11/5 졸업년도 계산기: 2001년생 입학 연기에 2008년 신설 신청 절차 안내가 나옴 — values 만 보면 못 잡음)
    const notes = (out.notes || []).join(' ');
    for (const want of c.notes_include || []) if (!notes.includes(want)) fail(id, `사례 ${i + 1} (${c.why}): 안내에 '${want}' 가 없음`);
    for (const bad of c.notes_exclude || []) if (notes.includes(bad)) fail(id, `사례 ${i + 1} (${c.why}): 안내에 '${bad}' 가 나오면 안 됨`);
    if (!Array.isArray(out.rows) || !out.rows.length) fail(id, `사례 ${i + 1}: 화면에 보여줄 rows 가 없음`);
    checked += 1;
  }
}

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}
console.log(`계산기 ${ids.length}개, 사례 ${checked}개 통과`);
