/*
 * 퍼스트랩 계산기 공통 틀 (화면 + 날짜·금액 도우미).
 *
 * 계산기 하나 = 파일 3개 (같은 id)
 *   defs/<id>.js    입력 항목 + compute()  → FirstLabCalc.register({...})
 *   data/<id>.json  요율·금액, 기준일(basis_date), 출처(sources)  ← 해마다 바뀌는 값은 여기만 고친다
 *   tests/<id>.json 검증 사례 (공식 예시·법령 문구 기준)  → node tests/test_calculators.mjs 로 배포 전 검사
 * 본문에는 독립된 줄에 [firstlab_calc id="<id>"] (mu-plugins/first-lab-calculators.php 가 아래 div 로 바꿈)
 *
 * compute(values, data, util) 는 { values, rows, notes } 를 돌려준다.
 *   values: 검사용 값 (숫자·날짜 문자열), rows: 화면 표 [[항목, 내용], ...], notes: 안내 문장 목록
 *   입력이 모자라면 throw new util.InputError('안내 문장')
 */
(function (root) {
  'use strict';

  const DAY_MS = 86400000;

  class InputError extends Error {}

  const util = {
    InputError,
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
    /** 방문자 기기 기준 오늘 'YYYY-MM-DD' */
    todayIso() {
      const d = new Date();
      return util.isoDate(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
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

  function field(input, uid) {
    const id = `${uid}-${input.name}`;
    let control;
    if (input.type === 'select') {
      control = el('select', { id, name: input.name },
        input.options.map(([value, label]) => el('option', { value }, label)));
      control.value = input.default || input.options[0][0];
    } else {
      const type = input.type === 'date' ? 'date' : 'number';
      control = el('input', { id, name: input.name, type, min: input.min, step: input.step, inputmode: type === 'number' ? 'numeric' : null });
      control.value = input.default === 'today' ? util.todayIso() : (input.default ?? '');
    }
    return el('div', { class: 'firstlab-calc__field' },
      el('label', { for: id }, input.label),
      control,
      input.help ? el('p', { class: 'firstlab-calc__help' }, input.help) : null);
  }

  function render(out, result) {
    out.replaceChildren(
      el('table', { class: 'firstlab-calc__table' },
        el('tbody', null, result.rows.map(([k, v]) => el('tr', null, el('th', { scope: 'row' }, k), el('td', null, v))))),
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
    if (!def || !data) {
      box.textContent = '계산기를 불러오지 못했습니다.';
      return;
    }
    const uid = `fc${++seq}`;
    const form = el('form', { class: 'firstlab-calc__form', novalidate: '' }, def.inputs.map((i) => field(i, uid)));
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
