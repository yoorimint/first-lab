/* 연장근로수당 계산기: 통상시급 × 시간 × 가산율. 기준값은 data/overtime-pay.json
 * 근거: 근로기준법 제56조(연장 50%·야간 50%·휴일 8시간 이내 50%, 8시간 초과 100% 가산), 제11조·시행령 제7조 별표 1(상시 4명 이하 사업장은 제56조 미적용),
 * 시행령 제6조②(시급제는 그 금액, 월급제는 월급 ÷ 월 통상임금 산정 기준시간 수 — 주 40시간이면 209시간, 고용노동부).
 * 각 줄은 원 미만 버림, 합계는 줄의 합. */
(function () {
  'use strict';

  const num = (x) => Math.round(x * 100) / 100;

  FirstLabCalc.register({
    id: 'overtime-pay',
    title: '연장근로수당 계산기',
    intro: '통상임금과 연장·야간·휴일에 일한 시간을 넣으면 가산수당까지 더한 금액이 나와요.',
    inputs: [
      {
        name: 'pay_type', label: '임금 형태', type: 'select', default: 'monthly',
        options: [['monthly', '월급 (월 통상임금)'], ['hourly', '시급']],
      },
      { name: 'amount', label: '월 통상임금 또는 시급 (원)', type: 'number', min: 1, step: 1, default: 2500000, help: '기본급에 매달 정해진 대로 모두에게 주는 고정 수당을 더한 금액이에요. 연장수당·실적 성과급은 빼요.' },
      { name: 'base_hours', label: '월 통상임금 산정 기준시간 (월급일 때)', type: 'number', min: 1, step: 'any', default: 209, help: '주 40시간·주휴 8시간이면 209시간이에요. 단시간이면 (1주 소정근로시간 + 주휴시간) × 365 ÷ 7 ÷ 12.' },
      { name: 'employees', label: '상시 근로자 수 (명)', type: 'number', min: 1, step: 1, default: 5, help: '4명 이하면 가산수당 규정이 적용되지 않아요.' },
      { name: 'ot_hours', label: '연장근로 시간 (평일, 시간)', type: 'number', min: 0, step: 0.5, default: 10, help: '하루 8시간·주 40시간을 넘은 시간이에요. 단시간근로자는 계약한 시간을 넘은 시간.' },
      { name: 'ot_night_hours', label: '그중 밤 10시~아침 6시 시간', type: 'number', min: 0, step: 0.5, default: 0 },
      { name: 'night_hours', label: '연장이 아닌 야간근로 시간', type: 'number', min: 0, step: 0.5, default: 0, help: '원래 근무시간 안에 들어 있는 밤 10시~아침 6시 근무예요. 기본 임금은 이미 받으니 가산분(50%)만 더해요.' },
      { name: 'holiday_hours', label: '휴일근로 하루 시간', type: 'number', min: 0, step: 0.5, default: 0, help: '주휴일·약정휴일에 일한 하루 시간이에요. 8시간까지 50%, 넘은 시간은 100% 가산.' },
      { name: 'holiday_days', label: '휴일근로한 날 수', type: 'number', min: 0, step: 1, default: 0 },
    ],

    compute(v, d, u) {
      const amount = Number(v.amount);
      const baseHours = Number(v.base_hours);
      const employees = Number(v.employees);
      const ot = Number(v.ot_hours);
      const otNight = Number(v.ot_night_hours);
      const night = Number(v.night_hours);
      const hh = Number(v.holiday_hours);
      const hd = Number(v.holiday_days);
      if (!(amount > 0) || !Number.isInteger(amount)) throw new u.InputError('통상임금(또는 시급)을 1원 단위로 넣어 주세요.');
      if (v.pay_type === 'monthly' && !(baseHours > 0 && baseHours <= 300)) throw new u.InputError('월 통상임금 산정 기준시간을 1~300시간으로 넣어 주세요.');
      if (!(employees >= 1) || !Number.isInteger(employees)) throw new u.InputError('상시 근로자 수를 1명 이상으로 넣어 주세요.');
      if ([ot, otNight, night, hh].some((x) => !(x >= 0))) throw new u.InputError('시간은 0 이상으로 넣어 주세요.');
      if (!(hd >= 0) || !Number.isInteger(hd)) throw new u.InputError('휴일근로한 날 수는 0 이상의 정수로 넣어 주세요.');
      if (hh > 24) throw new u.InputError('휴일근로 하루 시간은 24시간까지 넣어 주세요.');
      if (otNight > ot) throw new u.InputError('밤 시간은 연장근로 시간보다 클 수 없어요.');

      const hourly = v.pay_type === 'hourly' ? amount : amount / baseHours;
      const covered = employees >= d.min_employees;
      const within = Math.min(hh, d.holiday_split_hours) * hd;
      const over = Math.max(hh - d.holiday_split_hours, 0) * hd;

      const otPay = Math.floor(hourly * ot * (covered ? d.overtime_rate : 1));
      const otNightPay = covered ? Math.floor(hourly * otNight * d.night_add_rate) : 0;
      const nightPay = covered ? Math.floor(hourly * night * d.night_add_rate) : 0;
      const holidayPay = Math.floor(hourly * within * (covered ? d.holiday_rate_within : 1))
        + Math.floor(hourly * over * (covered ? d.holiday_rate_over : 1));
      const total = otPay + otNightPay + nightPay + holidayPay;

      const rows = [
        ['통상시급', v.pay_type === 'hourly' ? u.won(hourly) : `${u.won(Math.floor(hourly))} (${u.won(amount)} ÷ ${num(baseHours)}시간)`],
        ['연장근로수당', `${u.won(otPay)} (${num(ot)}시간 × ${covered ? '1.5배' : '1배'})`],
        ['연장 중 야간 가산', covered ? `${u.won(otNightPay)} (${num(otNight)}시간 × 0.5배)` : '0원 (4명 이하 사업장)'],
        ['야간근로 가산', covered ? `${u.won(nightPay)} (${num(night)}시간 × 0.5배)` : '0원 (4명 이하 사업장)'],
        ['휴일근로수당', `${u.won(holidayPay)} (8시간 이내 ${num(within)}시간 × ${covered ? '1.5배' : '1배'}, 초과 ${num(over)}시간 × ${covered ? '2배' : '1배'})`],
        ['합계', u.won(total)],
      ];
      const notes = [];
      if (!covered) notes.push('상시 4명 이하 사업장은 연장·야간·휴일 가산(50%·100%) 규정이 적용되지 않아요. 근로계약서·취업규칙에 가산을 정했다면 그 기준을 따라요.');
      if (hourly < d.min_wage_2026) notes.push(`통상시급이 2026년 최저임금(시급 ${u.won(d.min_wage_2026)})보다 낮아요. 통상임금이나 기준시간을 다시 확인해 보세요.`);
      notes.push(`연장근로는 당사자가 합의해도 1주 ${d.weekly_overtime_limit}시간까지예요 (휴일근로 포함).`);
      notes.push('휴일근로는 연장 가산과 겹쳐 받지 않아요. 휴일 밤 10시~아침 6시 근무는 야간 가산 칸에 따로 넣어요. 각 줄은 원 미만을 버렸어요.');
      return {
        values: { hourly: num(hourly), ot_pay: otPay, ot_night_pay: otNightPay, night_pay: nightPay, holiday_pay: holidayPay, total },
        rows,
        notes,
      };
    },
  });
})();
