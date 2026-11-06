/*
 * 퍼스트랩 계산기 공통 틀 (화면 + 날짜·금액 도우미).
 *
 * 계산기 하나 = 파일 3개 (같은 id)
 *   defs/<id>.js    입력 항목 + compute()  → FirstLabCalc.register({...})
 *   data/<id>.json  요율·금액, 기준일(basis_date), 출처(sources)  ← 해마다 바뀌는 값은 여기만 고친다
 *                   "scheduled": [{"from": "2027-01-01", "values": {키: 새 값}, "source": {"name", "url"}}]
 *                   = 이미 확정된 다음 값. 그날부터 화면이 자동으로 새 값을 쓴다 (util.dataOn) — 감시 세션 없이 연초를 넘기려고 (11/5)
 *                   "yearly_checked": 2027 = 연초에 바뀌는 값을 그 해 것으로 확인함 (해마다 안 바뀌는 계산기는 "yearly": false)
 *   tests/<id>.json 검증 사례 (공식 예시·법령 문구 기준)  → node tests/test_calculators.mjs 로 배포 전 검사
 * 본문에는 독립된 줄에 [firstlab_calc id="<id>"] (mu-plugins/first-lab-calculators.php 가 아래 div 로 바꿈)
 *
 * compute(values, data, util) 는 { values, rows, notes } 를 돌려준다.
 *   values: 검사용 값 (숫자·날짜 문자열), rows: 화면 표 [[항목, 내용], ...], notes: 안내 문장 목록
 *   rows 의 세 번째 값(선택)은 관련 글 주소 → 내용 뒤에 '관련 글 보기' 링크. 주소는 data 의 "links" 에 두면
 *   공개 안 된 글(초안·예약) 주소는 화면에 넣기 전에 빠진다 (mu-plugins/first-lab-calculators.php)
 *   입력이 모자라면 throw new util.InputError('안내 문장')
 * 입력 기본값 'today' = 한국 날짜, 'data:키' = 그날 적용되는 data 값 (해마다 바뀌는 값을 defs 에 박지 않는다)
 */
(function (root) {
  'use strict';

  const DAY_MS = 86400000;

  class InputError extends Error {}

  const FLOOR_REL = 1e-15;
  const util = {
    InputError,
    /** 숫자 칸 자판: 소수를 넣는 칸(step 이 소수·'any')은 'decimal'. 'numeric' 은 iOS 자판에 소수점이 없어 9,333.33·0.015 를 못 넣음 (11/5) */
    inputMode: (input) => (input.step !== undefined && !Number.isInteger(Number(input.step)) ? 'decimal' : 'numeric'),
    /** 버림 (컴퓨터 소수 오차 보정): 3,800,000 ÷ 209 × 11 × 1.5 = 299,999.99… 를 그냥 버리면 1원이 빠짐 (11/5 주휴·연장수당 계산기).
     *  계산기는 Math.floor/Math.trunc 대신 이것을 쓴다 — 검사기가 막음 (tests/test_calculators.mjs).
     *  보정 폭은 몇 번 계산한 오차(값의 1e-15배 + 1e-9)만큼 — 11/6 #1019: 1e-12배였을 때 999,999,999,999 ÷ 2,000 = 499,999,999.9995 를
     *  500,000,000 으로 올려 1천 원이 틀림 (값이 크면 보정이 진짜 끝수보다 커짐). */
    floor: (n) => Math.floor(n + Math.abs(n) * FLOOR_REL + 1e-9),
    /** 0 쪽으로 버림 (환급 등 음수), 같은 오차 보정 */
    trunc: (n) => (n < 0 ? -Math.floor(-n + Math.abs(n) * FLOOR_REL + 1e-9) : Math.floor(n + Math.abs(n) * FLOOR_REL + 1e-9)),
    /** 금액 → "23,000원" */
    won: (n) => Math.round(n).toLocaleString('ko-KR') + '원',
    /** 'YYYY-MM-DD' → 그날 0시(UTC) 밀리초. 형식이 틀리거나 없는 날짜면 null */
    parseDate(text) {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(text || ''));
      if (!m) return null;
      const t = Date.UTC(+m[1], +m[2] - 1, +m[3]);
      return util.isoDate(t) === m[0] ? t : null;
    },
    addDays: (t, days) => t + days * DAY_MS,
    daysBetween: (from, to) => Math.round((to - from) / DAY_MS),
    isoDate: (t) => new Date(t).toISOString().slice(0, 10),
    /** 화면용 "2026년 11월 4일" */
    korDate(t) {
      const d = new Date(t);
      return `${d.getUTCFullYear()}년 ${d.getUTCMonth() + 1}월 ${d.getUTCDate()}일`;
    },
    /** 그날 적용되는 data: scheduled 중 from <= 날짜인 것의 values 를 덮어쓰고, 기준일·출처도 바꿈 */
    dataOn(data, iso) {
      const out = { ...data, sources: [...(data.sources || [])] };
      const due = (data.scheduled || []).filter((s) => s.from <= iso).sort((a, b) => (a.from < b.from ? -1 : 1));
      for (const s of due) {
        Object.assign(out, s.values);
        if (s.from > out.basis_date) out.basis_date = s.from;
        if (s.source) out.sources.unshift(s.source);
      }
      delete out.scheduled;
      Object.defineProperty(out, 'raw', { value: data, enumerable: false }); // 사건 날짜(퇴사일 등)로 다시 고를 때 (dataOn(d.raw, 날짜))
      return out;
    },
    /** 방문자 기기 기준 오늘 'YYYY-MM-DD' */
    todayIso() {
      // 한국 날짜 (기기 시간대가 달라도 전환일 앞뒤로 값이 섞이지 않게 — 11/5 검토)
      try {
        return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
      } catch (e) {
        const d = new Date();
        return util.isoDate(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
      }
    },
  };

  const defs = {};
  root.FirstLabCalc = {
    register(def) { defs[def.id] = def; },
    defs,
    util,
  };

  if (typeof document === 'undefined') return; // 검사(node)에서는 화면 부분 생략

  let seq = 0;

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null) node.setAttribute(k, v);
    });
    children.flat(Infinity).forEach((c) => {
      if (c !== null && c !== undefined) node.append(c);
    });
    return node;
  }

  function field(input, uid, data) {
    const id = `${uid}-${input.name}`;
    let control;
    if (input.type === 'select') {
      control = el('select', { id, name: input.name },
        input.options.map(([value, label]) => el('option', { value }, label)));
      control.value = input.default || input.options[0][0];
    } else {
      const type = input.type === 'date' ? 'date' : 'number';
      control = el('input', { id, name: input.name, type, min: input.min, step: input.step, inputmode: type === 'number' ? util.inputMode(input) : null });
      // 'data:키' = 그날 적용되는 data 값 (최저임금처럼 해마다 바뀌는 기본값을 코드에 박지 않게 — 11/5 검토)
      const fromData = typeof input.default === 'string' && input.default.startsWith('data:') ? (data || {})[input.default.slice(5)] : undefined;
      control.value = input.default === 'today' ? util.todayIso() : (fromData ?? input.default ?? '');
    }
    return el('div', { class: 'firstlab-calc__field' },
      el('label', { for: id }, input.label),
      control,
      input.help ? el('p', { class: 'firstlab-calc__help' }, input.help) : null);
  }

  function render(out, result) {
    out.replaceChildren(
      el('table', { class: 'firstlab-calc__table' },
        el('tbody', null, result.rows.map(([k, v, href]) => el('tr', null, el('th', { scope: 'row' }, k),
          el('td', null, v, href ? [' ', el('a', { href }, '관련 글 보기')] : null))))),
      result.notes && result.notes.length
        ? el('ul', { class: 'firstlab-calc__notes' }, result.notes.map((n) => el('li', null, n)))
        : null);
  }

  function footer(data) {
    const links = (data.sources || []).map((s, i) => [
      i ? ', ' : '',
      el('a', { href: s.url, target: '_blank', rel: 'nofollow noopener' }, s.name),
    ]);
    return el('p', { class: 'firstlab-calc__source' },
      `기준일 ${data.basis_date} · 출처: `, links,
      '. 참고용 계산이며 실제 금액·날짜는 해당 기관 안내를 따릅니다.');
  }

  function mount(box) {
    const def = defs[box.dataset.calc];
    let data = null;
    try { data = JSON.parse(box.dataset.rates || 'null'); } catch (e) { data = null; }
    if (data) data = util.dataOn(data, util.todayIso());
    if (!def || !data) {
      box.textContent = '계산기를 불러오지 못했습니다.';
      return;
    }
    const uid = `fc${++seq}`;
    const form = el('form', { class: 'firstlab-calc__form', novalidate: '' }, def.inputs.map((i) => field(i, uid, data)));
    const out = el('div', { class: 'firstlab-calc__result', 'aria-live': 'polite' });

    const update = () => {
      const values = Object.fromEntries(new FormData(form).entries());
      try {
        render(out, def.compute(values, data, util));
      } catch (e) {
        if (!(e instanceof InputError)) throw e;
        out.replaceChildren(el('p', { class: 'firstlab-calc__hint' }, e.message));
      }
    };
    form.addEventListener('input', update);
    form.addEventListener('submit', (e) => { e.preventDefault(); update(); });

    box.replaceChildren(
      el('p', { class: 'firstlab-calc__title' }, def.title),
      def.intro ? el('p', { class: 'firstlab-calc__intro' }, def.intro) : null,
      form, out, footer(data));
    update();
  }

  const start = () => document.querySelectorAll('.firstlab-calc[data-calc]').forEach(mount);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})(typeof window !== 'undefined' ? window : globalThis);
