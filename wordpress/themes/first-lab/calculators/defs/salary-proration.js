/* 급여 일할 계산기: 달 중간에 입사·퇴사한 월급제 근로자의 그달 세전 월급.
   고용노동부 행정해석(근기 1455-24422, 1981.8.11.)·빠른인터넷상담: 법령에 정한 방법은 없고, 회사 규정이 있으면 그 규정,
   없으면 '월급 ÷ 그달 역일수 × 근무기간'(일반적) 또는 '월급 ÷ 소정근로일(유급 포함) × 근로일수(유급 포함)'.
   30일 고정은 회사 규정으로 정했을 때만. 어느 방식이든 시간급으로 최저임금 이상(2026 상담). 원 미만은 버림(법정 끝수 처리 없음 — 안내). */
(function () {
  'use strict';

  const METHOD = { calendar: '그달 날수', thirty: '30일', paid: '유급일' };

  FirstLabCalc.register({
    id: 'salary-proration',
    title: '급여 일할 계산기',
    intro: '달 중간에 입사하거나 퇴사한 달의 월급을 날짜로 나눠 계산해요. 넣은 첫날과 마지막 날은 둘 다 셈에 들어가요.',
    inputs: [
      { name: 'monthly', label: '한 달 월급 (세전, 원)', type: 'number', min: 0, step: 1, default: 3000000 },
      { name: 'start', label: '그달 첫 근무일', type: 'date', default: '2026-12-10', help: '입사한 달이면 입사일, 퇴사한 달이면 그달 1일을 넣어요.' },
      { name: 'end', label: '그달 마지막 근무일', type: 'date', default: '2026-12-31', help: '퇴사한 달이면 마지막으로 일한 날, 입사한 달이면 그달 말일을 넣어요.' },
      {
        name: 'method', label: '무엇으로 나눌까요?', type: 'choice', default: 'calendar',
        options: [['calendar', '그달 날수 (28~31일)'], ['thirty', '30일 (회사 규정)'], ['paid', '유급일 (유급일로 나눔)']],
        help: '회사 규정이 없으면 보통 그달 날수로 나눠요. 유급일 방식도 규정이 없을 때 쓸 수 있어요. 30일은 취업규칙이나 근로계약에 그렇게 정해 둔 경우예요.',
      },
      { name: 'paidTotal', label: '그달 유급일 수 (일)', type: 'number', min: 0, step: 1, default: 26, help: '유급일 방식만 써요. 일하기로 한 날에 주휴일처럼 돈이 나오는 쉬는 날을 더한 수예요.' },
      { name: 'paidWorked', label: '그중 재직한 유급일 (일)', type: 'number', min: 0, step: 1, default: 13, help: '유급일 방식만 써요. 그달 유급일 가운데 재직 기간에 든 날 수예요.' },
    ],

    compute(v, d, u) {
      const monthly = Number(v.monthly);
      if (!Number.isFinite(monthly) || monthly <= 0) throw new u.InputError('한 달 월급을 넣어 주세요.');
      if (monthly > d.max_monthly) throw new u.InputError('월급은 10억 원 안에서 넣어 주세요.');
      const start = u.parseDate(v.start);
      const end = u.parseDate(v.end);
      if (start === null) throw new u.InputError('그달 첫 근무일을 넣어 주세요.');
      if (end === null) throw new u.InputError('그달 마지막 근무일을 넣어 주세요.');
      if (end < start) throw new u.InputError('마지막 근무일이 첫 근무일보다 앞이에요.');
      const s = new Date(start);
      const e = new Date(end);
      if (s.getUTCFullYear() !== e.getUTCFullYear() || s.getUTCMonth() !== e.getUTCMonth()) {
        throw new u.InputError('첫 근무일과 마지막 근무일은 같은 달 안에서 넣어 주세요. 달마다 따로 계산해요.');
      }
      const method = v.method || 'calendar';
      if (!METHOD[method]) throw new u.InputError('나누는 방법을 골라 주세요.');

      const days = u.daysBetween(start, end) + 1;
      const monthDays = new Date(Date.UTC(s.getUTCFullYear(), s.getUTCMonth() + 1, 0)).getUTCDate();
      const fullMonth = days === monthDays;

      const calendarPay = fullMonth ? monthly : u.floor((monthly * days) / monthDays);
      const thirtyPay = fullMonth ? monthly : u.floor((monthly * days) / 30);
      let paidPay = null;
      let paidTotal = null;
      let paidWorked = null;
      if (method === 'paid') {
        paidTotal = Number(v.paidTotal);
        paidWorked = Number(v.paidWorked);
        if (!Number.isInteger(paidTotal) || paidTotal <= 0 || paidTotal > 31) throw new u.InputError('그달 유급일 수를 1~31일로 넣어 주세요.');
        if (!Number.isInteger(paidWorked) || paidWorked < 0) throw new u.InputError('재직한 유급일을 0일 이상으로 넣어 주세요.');
        if (paidWorked > paidTotal) throw new u.InputError('재직한 유급일이 그달 유급일 수보다 많아요.');
        paidPay = paidWorked === paidTotal ? monthly : u.floor((monthly * paidWorked) / paidTotal);
      }
      const pay = method === 'calendar' ? calendarPay : method === 'thirty' ? thirtyPay : paidPay;

      const formula = fullMonth && method !== 'paid'
        ? '한 달을 모두 재직해 월급 전액'
        : method === 'calendar'
          ? `${u.won(monthly)} × ${days}일 ÷ ${monthDays}일`
          : method === 'thirty'
            ? `${u.won(monthly)} × ${days}일 ÷ 30일`
            : `${u.won(monthly)} × ${paidWorked}일 ÷ ${paidTotal}일`;

      const rows = [
        ['재직 일수 (첫날·마지막 날 포함)', `${days}일`],
        ['그달 날수', `${monthDays}일`],
        [`일할 월급 (${METHOD[method]} 방식, 세전)`, u.won(pay)],
        ['계산식', formula],
      ];
      if (method !== 'calendar') rows.push(['참고: 그달 날수로 나누면', u.won(calendarPay)]);
      if (method !== 'thirty') rows.push(['참고: 30일로 나누면', u.won(thirtyPay)]);

      const notes = [];
      if (fullMonth && method !== 'paid') notes.push('그달을 처음부터 끝까지 다녔다면 일할 계산 없이 정해진 월급을 받아요.');
      if (method === 'thirty' && !fullMonth && monthDays < 30) notes.push('2월처럼 30일보다 짧은 달은 30일로 나누면 그달 날수로 나눌 때보다 적게 나와요.');
      if (method === 'thirty' && !fullMonth && monthDays === 31) notes.push('31일인 달은 30일로 나누면 그달 날수로 나눌 때보다 많이 나와요.');
      notes.push('회사 취업규칙·근로계약에 정한 방법이 있으면 그 방법이 먼저예요. 정한 것이 없으면 보통 그달 날수로 나눠요.');
      if (method === 'paid') notes.push('유급일 방식은 날짜가 아니라 넣은 유급일 수로 계산해요.');
      notes.push('어느 방식이든 시간급으로 따져 최저임금보다 적으면 안 돼요.');
      notes.push('결근한 날이 있으면 그날과 그 주 주휴일이 빠질 수 있어요. 이 계산은 결근을 빼지 않았어요.');
      notes.push('세금·4대보험을 떼기 전 금액이에요. 원 미만은 버렸고, 회사 규정에 따라 끝수 처리가 다를 수 있어요.');

      return {
        values: { days, monthDays, fullMonth, pay, calendarPay, thirtyPay, paidPay },
        rows,
        notes,
      };
    },
  });
})();
