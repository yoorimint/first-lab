/* 실업급여 근무일수(피보험 단위기간) 계산기: 이직일 이전 18개월(기준기간) 안에서 보수 지급의 기초가 된 날을 센다 (고용보험법 제40조②·제41조①).
   근무일과 유급휴일은 넣고, 무급휴일(주 5일제의 무급 토요일 등)은 뺀다 (고용노동부 빠른인터넷상담 답변).
   평일 공휴일은 근무 요일이면 유급으로 보고 센다 — 무급이었다면 '무급으로 빠진 날'에 넣게 안내한다. 값은 data/insured-unit-days.json */
(function () {
  'use strict';

  // 요일 번호: 0=일 1=월 … 6=토. 근무일은 월요일부터 N일, 유급 주휴일은 일요일로 둔다.
  function paidWeekdays(pattern, n) {
    if (pattern === 'w5') return [1, 2, 3, 4, 5, 0];
    if (pattern === 'w5p' || pattern === 'w6') return [0, 1, 2, 3, 4, 5, 6];
    const days = [];
    for (let i = 1; i <= n; i += 1) days.push(i);
    if (pattern === 'part') days.push(0);
    return days;
  }

  // 기간 계산: 이직일 다음 날에서 18개월 전 = 기준기간 첫날 (그날부터 18개월이 이직일에 끝남). 없는 날(2월 30일 등)은 다음 달 1일.
  function monthsBefore(t, months) {
    const d = new Date(t);
    let y = d.getUTCFullYear();
    let m = d.getUTCMonth() - months;
    while (m < 0) { m += 12; y -= 1; }
    const day = d.getUTCDate();
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    if (day > last) return Date.UTC(y, m + 1, 1);
    return Date.UTC(y, m, day);
  }

  FirstLabCalc.register({
    id: 'insured-unit-days',
    title: '실업급여 근무일수(피보험 단위기간) 계산기',
    intro: '입사일과 퇴사일(마지막 근무일), 일하는 형태를 넣으면 실업급여 조건인 180일에 들어가는 날을 세어 줘요. 일한 날과 유급휴일은 세고, 무급휴일은 빼요.',
    inputs: [
      { name: 'start', label: '고용보험 가입일 (입사일)', type: 'date', default: '2026-03-02' },
      { name: 'quit', label: '이직일 (퇴사 전 마지막 근무일)', type: 'date', default: 'today' },
      {
        name: 'pattern', label: '일하는 형태', type: 'select', default: 'w5',
        options: [
          ['w5', '주 5일 · 토요일 무급 휴무 · 일요일 유급 주휴'],
          ['w5p', '주 5일 · 토요일·일요일 모두 유급'],
          ['w6', '주 6일 · 일요일 유급 주휴'],
          ['part', '단시간 · 주 15시간 이상 (주휴 유급)'],
          ['mini', '초단시간 · 주 15시간 미만 (주휴 없음)'],
        ],
        help: '토요일이 유급인지는 근로계약서나 취업규칙에서 확인해요.',
      },
      { name: 'days', label: '단시간이면 한 주에 일하는 날 수', type: 'number', min: 1, max: 6, step: 1, default: 3, help: '단시간·초단시간을 골랐을 때만 써요.' },
      { name: 'unpaid', label: '그 사이 무급으로 빠진 날 수', type: 'number', min: 0, step: 1, default: 0, help: '기준기간 안에서 무급휴직, 결근해 임금에서 뺀 날, 무급이었던 공휴일 등이에요.' },
    ],

    compute(v, d, u) {
      const start = u.parseDate(v.start);
      const quit = u.parseDate(v.quit);
      if (start === null) throw new u.InputError('고용보험 가입일을 넣어 주세요.');
      if (quit === null) throw new u.InputError('이직일을 넣어 주세요.');
      if (quit < start) throw new u.InputError('이직일이 가입일보다 앞이에요.');
      if (u.daysBetween(start, quit) > d.max_days) throw new u.InputError('기간은 10년 안에서 넣어 주세요.');
      const pattern = v.pattern || 'w5';
      if (!['w5', 'w5p', 'w6', 'part', 'mini'].includes(pattern)) throw new u.InputError('일하는 형태를 골라 주세요.');
      const n = Number(v.days);
      if ((pattern === 'part' || pattern === 'mini') && !(Number.isInteger(n) && n >= 1 && n <= 6)) {
        throw new u.InputError('한 주에 일하는 날 수는 1~6일로 넣어 주세요.');
      }
      const unpaid = v.unpaid === '' || v.unpaid === undefined ? 0 : Number(v.unpaid);
      if (!(Number.isInteger(unpaid) && unpaid >= 0)) throw new u.InputError('무급으로 빠진 날 수는 0 이상의 정수로 넣어 주세요.');

      const paid = paidWeekdays(pattern, n);
      const isPaid = (t) => paid.includes(new Date(t).getUTCDay());
      const countBetween = (from, to) => {
        let c = 0;
        for (let t = from; t <= to; t = u.addDays(t, 1)) if (isPaid(t)) c += 1;
        return c;
      };

      const windowStart = monthsBefore(u.addDays(quit, 1), d.base_months);
      const from = Math.max(start, windowStart);
      const counted = countBetween(from, quit);
      const unitDays = Math.max(0, counted - unpaid);
      const met = unitDays >= d.required_days;
      const shortfall = met ? 0 : d.required_days - unitDays;

      // 같은 형태로 계속 다녔다면 180일을 채우는 날 (그날 기준 18개월 안, 무급으로 빠진 날도 빼고)
      let reach = null;
      for (let t = start, c = 0; u.daysBetween(start, t) <= d.reach_search_days; t = u.addDays(t, 1)) {
        if (isPaid(t)) c += 1;
        const ws = monthsBefore(u.addDays(t, 1), d.base_months);
        const inWindow = ws > start ? countBetween(ws, t) : c;
        if (inWindow - unpaid >= d.required_days) { reach = t; break; }
      }

      const perWeek = paid.length;
      const rows = [
        ['기준기간 (이직일 이전 18개월)', `${u.korDate(windowStart)} ~ ${u.korDate(quit)}`],
        ['센 기간', `${u.korDate(from)} ~ ${u.korDate(quit)}`],
        ['한 주에 세는 날', `${perWeek}일`],
        ['근무일 + 유급휴일', `${counted.toLocaleString('ko-KR')}일`],
        ['무급으로 빠진 날', `${unpaid.toLocaleString('ko-KR')}일`],
        ['피보험 단위기간', `${unitDays.toLocaleString('ko-KR')}일`],
        ['180일 조건', met ? '채웠어요' : `${shortfall.toLocaleString('ko-KR')}일 모자라요`],
      ];
      if (!met) rows.push(['같은 형태로 계속 다니면', reach !== null ? `${u.korDate(reach)}에 180일` : '18개월 안에 180일을 못 채워요']);

      const notes = [];
      notes.push('180일은 실업급여를 받을 수 있는 조건 중 하나예요. 퇴사 사유, 일할 의사와 능력, 재취업 노력 같은 다른 조건도 갖춰야 해요.');
      notes.push('평일 공휴일은 유급으로 보고 셌어요. 무급이었다면 그날 수를 무급으로 빠진 날에 더해 주세요.');
      if (pattern === 'w5') notes.push('무급인 토요일에 출근해 일한 날은 세지 않았어요. 그날이 들어가는지는 이직확인서나 관할 고용센터에서 확인해요.');
      if (pattern === 'mini' && n <= 2) notes.push('주 15시간 미만이고 주 2일 이하로 일했다면, 조건에 따라 18개월 대신 24개월 안에서 볼 수 있어요. 이 계산기는 18개월로만 세요.');
      if (start < windowStart) notes.push('가입일이 기준기간보다 앞이라 기준기간 안의 날만 셌어요.');
      notes.push('전에 실업급여(구직급여)를 받았다면, 그 실업급여와 관련해 고용보험 자격을 잃은 날 이전 기간은 다시 세지 않아요. 그 뒤 가입일을 넣어 주세요.');
      notes.push('정확한 날 수는 회사가 낸 이직확인서의 보수 지급 기초일수로 확인해요. 기간 끝의 하루 이틀은 다를 수 있어요.');

      return {
        values: {
          windowStart: u.isoDate(windowStart), from: u.isoDate(from), perWeek, counted, unpaid, unitDays, met, shortfall,
          reach: reach === null ? null : u.isoDate(reach),
        },
        rows,
        notes,
      };
    },
  });
})();
