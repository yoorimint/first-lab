/* 알바 급여 계산기: 시급·1주 소정근로시간·초과근로·야간근로 → 한 주·한 달 세전 급여. 기준값은 data/part-time-pay.json
 * 근거: 근로기준법 제18조③(4주 평균 1주 15시간 미만은 주휴 제외)·제55조①·제56조①③(연장·야간 50% 가산, 상시 4명 이하 미적용 — 제11조·시행령 별표 1),
 * 기간제 및 단시간근로자 보호 등에 관한 법률 제6조①③(단시간근로자 소정근로시간 초과근로 1주 12시간 한도·50% 가산, 같은 법 제3조 상시 5명 이상).
 * 주휴시간 = (1주 소정근로시간 ÷ 40) × 8시간, 8시간 상한 (주휴수당 계산기와 같은 식, 통상근로자 주 5일·40시간 사업장 기준).
 * 한 달 = 한 주 합계 × 365 ÷ 7 ÷ 12 (평균), 항목마다 원 미만 버림. */
(function () {
  'use strict';

  const hoursText = (h) => `${Math.round(h * 100) / 100}시간`;

  FirstLabCalc.register({
    id: 'part-time-pay',
    title: '알바 급여 계산기',
    intro: '시급과 1주에 일하기로 정한 시간을 넣으면 주휴수당을 더한 한 주·한 달 세전 급여가 나와요.',
    inputs: [
      { name: 'wage', label: '시급 (원)', type: 'number', min: 1, step: 1, default: 'data:min_wage', help: '처음 값은 올해 최저임금이에요. 내 시급으로 바꿔 넣어요.' },
      { name: 'hours', label: '1주 소정근로시간 (시간)', type: 'number', min: 0, step: 0.5, default: 20, help: '근로계약서에 정한 1주 근로시간이에요 (휴게시간 빼고). 주마다 다르면 4주 평균을 넣어요.' },
      { name: 'extra', label: '1주 소정근로시간을 넘겨 더 일한 시간 (시간)', type: 'number', min: 0, step: 0.5, default: 0, help: '계약한 시간보다 더 일한 시간이에요. 없으면 0.' },
      { name: 'night', label: '그중 밤 10시~아침 6시에 일한 시간 (시간)', type: 'number', min: 0, step: 0.5, default: 0, help: '위 두 칸에 넣은 시간 중 야간에 일한 시간이에요. 없으면 0.' },
      {
        name: 'size', label: '사업장 상시 근로자 수', type: 'select', default: 'five',
        options: [['five', '5명 이상'], ['small', '4명 이하']],
      },
    ],

    compute(v, d, u) {
      const f = FirstLabCalc.util.floor;
      const wage = Number(v.wage);
      const blank = (x) => String(x ?? '').trim() === '';
      if (!(wage > 0) || !Number.isInteger(wage)) throw new u.InputError('시급을 1원 단위로 넣어 주세요.');
      if (blank(v.hours)) throw new u.InputError('1주 소정근로시간을 넣어 주세요.');
      // 0.01시간 단위로 먼저 맞춤 — 금액(100배 정수)과 15시간 판정이 같은 값을 보게 (11/7 외부 검수: 14.995 → 급여 15시간분인데 주휴 0)
      const r2 = (x) => Math.round(x * 100) / 100;
      const hours = r2(Number(v.hours));
      const extra = blank(v.extra) ? 0 : r2(Number(v.extra));
      const night = blank(v.night) ? 0 : r2(Number(v.night));
      if (!(hours >= 0) || hours > d.full_week_hours) throw new u.InputError(`1주 소정근로시간은 0~${d.full_week_hours}시간으로 넣어 주세요 (법정 근로시간이 1주 ${d.full_week_hours}시간이에요).`);
      if (!(extra >= 0) || extra > d.max_extra_hours) throw new u.InputError(`더 일한 시간은 0~${d.max_extra_hours}시간으로 넣어 주세요.`);
      if (!(night >= 0) || night > hours + extra) throw new u.InputError('야간에 일한 시간은 소정근로시간과 더 일한 시간을 합친 것보다 클 수 없어요.');
      const big = v.size === 'five';
      // 시간은 0.5 단위라 100배 정수로 곱한 뒤 나눔 (소수 오차로 1원 빠지지 않게)
      const c = (h) => Math.round(h * 100);
      const holidayCenti = hours >= d.min_weekly_hours ? (c(hours) * d.full_day_hours) / d.full_week_hours : 0;
      const basePay = f((c(hours) * wage) / 100);
      const holidayPay = f((holidayCenti * wage) / 100);
      const extraPay = f((c(extra) * wage * (big ? d.extra_rate_per_100 : 100)) / 10000);
      const nightPay = big ? f((c(night) * wage * d.night_add_per_100) / 10000) : 0;
      const weekly = basePay + holidayPay + extraPay + nightPay;
      const monthly = f((weekly * 365) / 84);
      const rows = [
        ['소정근로 급여', `${u.won(basePay)} (${hoursText(hours)} × ${u.won(wage)})`],
        ['주휴수당', holidayCenti ? `${u.won(holidayPay)} (주휴 ${hoursText(holidayCenti / 100)})` : '0원'],
      ];
      if (extra > 0) rows.push([big ? '더 일한 시간 급여 (1.5배)' : '더 일한 시간 급여', u.won(extraPay)]);
      if (night > 0 && big) rows.push(['야간 가산 (0.5배)', u.won(nightPay)]);
      rows.push(['한 주 급여 (세전)', u.won(weekly)]);
      rows.push(['한 달 평균 급여 (세전)', u.won(monthly)]);
      const notes = [];
      if (hours < d.min_weekly_hours) notes.push(`1주 소정근로시간이 ${d.min_weekly_hours}시간 미만이라 주휴수당이 없어요 (4주 평균 기준).`);
      // 기간제법 제6조는 상시 5명 이상(같은 법 제3조, 시행령 별표 1), 연장근로 1주 12시간 한도는 근로기준법 제53조 (4명 이하 미적용) — 11/7 독립 검수
      if (big && hours > 0 && hours < d.full_week_hours && extra > d.part_time_extra_limit) notes.push(`단시간근로자는 소정근로시간을 넘는 근로가 1주 ${d.part_time_extra_limit}시간까지이고, 근로자 동의가 있어야 해요.`);
      if (big && hours >= d.full_week_hours && extra > d.part_time_extra_limit) notes.push(`1주 40시간을 넘는 연장근로는 당사자가 합의해도 1주 ${d.part_time_extra_limit}시간까지가 원칙이에요 (근로기준법 제53조①). 고용노동부 인가를 받은 특별연장근로나 운송업·보건업 특례(제59조)는 예외예요.`);
      if (!big && (extra > 0 || night > 0)) notes.push('상시 4명 이하 사업장은 근로기준법의 연장·야간 50% 가산이 적용되지 않아 1배로 계산했어요. 계약서·취업규칙에 가산을 정했으면 그대로 받아요.');
      if (wage < d.min_wage) notes.push(`${d.min_wage_year}년 최저임금(시급 ${u.won(d.min_wage)})보다 낮은 시급이에요.`);
      notes.push('소정근로일을 모두 나온 주 기준이에요. 결근한 주는 그 주 주휴수당이 빠져요.');
      notes.push('주휴시간은 같은 일을 하는 통상근로자가 주 5일·40시간 일하는 사업장 기준(주 시간 ÷ 40 × 8)이에요 (근로기준법 시행령 별표 2).');
      notes.push('한 달 평균은 한 주 급여 × 365 ÷ 7 ÷ 12로 계산했고, 원 미만은 버렸어요. 실제 달 급여는 그달에 든 근무일 수에 따라 달라져요.');
      notes.push('4대보험·세금을 떼기 전 금액이에요.');
      return {
        values: { holiday_hours: holidayCenti / 100, base_pay: basePay, holiday_pay: holidayPay, extra_pay: extraPay, night_pay: nightPay, weekly, monthly },
        rows, notes,
      };
    },
  });
})();
