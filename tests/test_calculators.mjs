// 계산기 배포 전 검사 (GitHub Actions). 실행: node tests/test_calculators.mjs
// 계산기마다 defs/<id>.js + data/<id>.json(기준일·출처) + tests/<id>.json(사례 3개 이상)이 있어야 하고,
// 사례의 expect 값이 compute() 의 values 와 모두 같아야 한다. 하나라도 틀리면 배포가 막힌다.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIR = path.join(ROOT, 'wordpress/themes/first-lab/calculators');
const MIN_CASES = 3;

const context = vm.createContext({ console });
const load = (file) => vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
load(path.join(DIR, 'calc.js'));
const { defs, util } = context.FirstLabCalc;

const failures = [];
const fail = (id, msg) => failures.push(`[${id}] ${msg}`);
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

const ids = fs.readdirSync(path.join(DIR, 'defs')).filter((f) => f.endsWith('.js')).map((f) => f.slice(0, -3));
let checked = 0;

for (const id of ids) {
  load(path.join(DIR, 'defs', `${id}.js`));
  const def = defs[id];
  if (!def) { fail(id, `defs/${id}.js 가 id '${id}' 로 register 하지 않음`); continue; }

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
  if (!Array.isArray(cases) || cases.length < MIN_CASES) fail(id, `검증 사례가 ${MIN_CASES}개 이상 필요함`);

  for (const [i, c] of (cases || []).entries()) {
    let out;
    try {
      out = def.compute(c.input, data, util);
    } catch (e) {
      fail(id, `사례 ${i + 1} 계산 오류: ${e.message}`);
      continue;
    }
    for (const [key, want] of Object.entries(c.expect || {})) {
      const got = out.values[key];
      if (got !== want) fail(id, `사례 ${i + 1} (${c.why}): ${key} = ${JSON.stringify(got)}, 기대값 ${JSON.stringify(want)}`);
    }
    if (!Array.isArray(out.rows) || !out.rows.length) fail(id, `사례 ${i + 1}: 화면에 보여줄 rows 가 없음`);
    checked += 1;
  }
}

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}
console.log(`계산기 ${ids.length}개, 사례 ${checked}개 통과`);
