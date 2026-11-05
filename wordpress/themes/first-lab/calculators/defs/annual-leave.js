/* 연차 개수 계산기: 입사일·기준일 → 지금까지 생긴 연차와 다음 연차가 생기는 날. 기준값은 data/annual-leave.json
 * 근거: 근로기준법 제60조①(1년 80% 이상 출근 15일)·②(1년 미만 1개월 개근 1일)·④(3년 이상 매 2년 1일 가산, 25일 한도)
 * 발생 시점: 1개월·1년의 근로를 마친 다음 날 (대법원 2021다227100, 법제처 24-0674 해석)
 *   → 입사일과 같은 날짜(그 달에 없으면 다음 달 1일)에 생김. 그날까지 재직해야 함 (1년 계약 만료면 11일)
 * 기간 끝 계산: 민법 제160조 (그 달에 해당일이 없으면 말일에 만료 → 다음 날 발생). 입사일 기준, 매년 80% 출근·매월 개근 가정. */
(function () {
  'use strict';

  /** 입사일 + k개월의 같은 날짜. 그 달에 그 날짜가 없으면 다음 달 1일 (민법 제160조③: 말일 만료 → 다음 날 발생) */
  function plusMonths(t, k) {
    const d = new Date(t);
    const y = d.getUTCFullYear();
    const m = d.getUTCMonth() + k;
    const day = d.getUTCDate();
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return day <= last ? Date.UTC(y, m, day) : Date.UTC(y, m + 1, 1);
  }

  FirstLabCalc.register({
    id: 'annual-leave',
    title: '연차 개수 계산기',
    intro: '입사일과 기준일을 넣으면 입사일 기준으로 지금까지 생긴 연차와 다음 연차가 생기는 날이 나와요.',
    inputs: [
      { name: 'start', label: '입사일', type: 'date', default: '2024-03-04' },
      { name: 'base', label: '기준일 (오늘, 퇴사한다면 근로관계가 끝나는 마지막 재직일)', type: 'date', default: 'today', help: '마지막으로 일한 날 뒤에 주말·휴일이 있어도 근로관계가 그날까지 이어지면 그 날짜를 넣어요 (연차가 생기는 날에 근로관계가 있어야 함).' },
    ],

    compute(v, d, u) {
      const start = u.parseDate(v.start);
      const base = u.parseDate(v.base === 'today' ? u.todayIso() : v.base);
      if (start === null) throw new u.InputError('입사일을 넣어 주세요.');
      if (base === null) throw new u.InputError('기준일을 넣어 주세요.');
      if (base < start) throw new u.InputError('기준일이 입사일보다 앞이에요.');

      let months = 0; // 근속 개월 수 (입사일 + k개월 날짜가 기준일 이하인 최대 k)
      while (plusMonths(start, months + 1) <= base) months += 1;
      const years = FirstLabCalc.util.floor(months / 12);
      const daysFor = (k) => Math.min(d.max_days, d.base_days + FirstLabCalc.util.floor((k - 1) / 2)); // k번째 해 연차
      const monthly = Math.min(months, d.first_year_max); // 1년 미만 1개월 개근마다 1일, 최대 11일
      let annualTotal = 0;
      for (let k = 1; k <= years; k += 1) annualTotal += daysFor(k);
      const current = years >= 1 ? daysFor(years) : 0;
      const total = monthly + annualTotal;

      const nextMonths = years >= 1 ? (years + 1) * 12 : months + 1;
      const nextDate = plusMonths(start, nextMonths);
      const nextDays = nextMonths % 12 === 0 ? daysFor(nextMonths / 12) : 1;

      const rows = [
        ['근속 기간', `${years}년 ${months % 12}개월`],
        ['1년 미만 때 생긴 연차 (1개월 개근마다 1일)', `${monthly}일`],
        ['올해 연차 (가장 최근 입사 기념일에 생긴 것)', years >= 1 ? `${current}일 (${u.korDate(plusMonths(start, years * 12))} 발생)` : '아직 없음 (1년이 지난 다음 날 15일)'],
        ['입사 후 생긴 연차 합계', `${total}일`],
        ['다음 연차가 생기는 날', `${u.korDate(nextDate)} · ${nextDays}일 (그날까지 재직할 때)`],
      ];
      const notes = [
        '매달 개근하고 해마다 80% 이상 출근했다고 보고 계산했어요. 80% 미만인 해는 그다음 해 연차가 15일이 아니라 개근한 달마다 1일이에요.',
        '상시 5명 이상 사업장, 1주 소정근로시간 15시간 이상인 근로자 기준이에요. 단시간 근로자는 통상근로자와 비교한 근로시간 비율로 줄어요.',
        '연차는 생긴 날부터 1년 안에 쓰지 않으면 없어지고, 1년 미만 때 생긴 연차는 최초 1년의 근로가 끝나는 날(입사일 1년 뒤의 전날)까지 써야 해요. 쓰지 못하고 없어진 연차는 사용 촉진을 안 했다면 수당으로 받아요.',
      ];
      if (months < 12 && months >= d.first_year_max) notes.unshift(`입사 1년을 채우고 다음 날(${u.korDate(plusMonths(start, 12))})에도 재직하면 15일이 더 생겨요. 1년 계약이 끝나 그 전에 퇴사하면 11일까지만이에요.`);
      return { values: { months, years, monthly, current, total, next_date: u.isoDate(nextDate), next_days: nextDays }, rows, notes };
    },
  });
})();
