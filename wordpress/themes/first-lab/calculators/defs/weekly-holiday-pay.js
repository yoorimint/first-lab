/* 주휴수당 계산기: 1주 소정근로시간·시급 → 주휴시간·주휴수당. 기준값은 data/weekly-holiday-pay.json
 * 근거: 근로기준법 제55조①·제18조③, 같은 법 시행령 제30조①·별표 2(단시간근로자 1일 소정근로시간 = 4주 소정근로시간 ÷ 통상근로자 4주 소정근로일 수)
 * 통상근로자가 주 5일·40시간인 사업장 기준으로 (1주 소정근로시간 ÷ 40) × 8시간, 8시간 상한. */
(function () {
  'use strict';

  const hoursText = (h) => `${Math.round(h * 100) / 100}시간`;

  FirstLabCalc.register({
    id: 'weekly-holiday-pay',
    title: '주휴수당 계산기',
    intro: '시급과 1주에 일하기로 정한 시간(소정근로시간)을 넣으면 한 주 주휴수당이 나와요.',
    inputs: [
      { name: 'wage', label: '시급 (원)', type: 'number', min: 1, step: 1, default: 10320, help: '2026년 최저임금은 시급 10,320원이에요.' },
      { name: 'hours', label: '1주 소정근로시간 (시간)', type: 'number', min: 0, step: 0.5, default: 20, help: '근로계약서에 정한 1주 근로시간이에요. 연장근로는 빼고, 주마다 다르면 4주 평균을 넣어요.' },
      {
        name: 'attended', label: '그 주 소정근로일을 모두 나왔나요', type: 'select', default: 'yes',
        options: [['yes', '네, 다 나왔어요 (지각·조퇴는 결근 아님)'], ['no', '아니요, 결근한 날이 있어요']],
      },
    ],

    compute(v, d, u) {
      const wage = Number(v.wage);
      const hours = Number(v.hours);
      if (!(wage > 0) || !Number.isInteger(wage)) throw new u.InputError('시급을 1원 단위로 넣어 주세요.');
      if (!(hours >= 0) || hours > d.max_input_hours) throw new u.InputError(`1주 소정근로시간은 0~${d.max_input_hours}시간으로 넣어 주세요.`);
      const notes = [];
      let holidayHours = 0;
      let reason = '';
      if (hours < d.min_weekly_hours) reason = `1주 소정근로시간이 ${d.min_weekly_hours}시간 미만이라 주휴수당이 없어요 (4주 평균 기준).`;
      else if (v.attended !== 'yes') reason = '소정근로일에 결근한 날이 있는 주는 그 주 주휴수당이 없어요.';
      else holidayHours = (Math.min(hours, d.full_week_hours) / d.full_week_hours) * d.full_day_hours;

      const pay = Math.floor(holidayHours * wage);
      const monthly = Math.floor((pay * 365) / 84);
      const rows = [
        ['주휴시간', holidayHours ? `${hoursText(holidayHours)} (${hoursText(Math.min(hours, d.full_week_hours))} ÷ ${d.full_week_hours} × ${d.full_day_hours})` : '0시간'],
        ['한 주 주휴수당', u.won(pay)],
        ['한 달 평균 주휴수당 (매주 받을 때)', u.won(monthly)],
      ];
      if (reason) notes.push(reason);
      if (hours > d.full_week_hours) notes.push(`${d.full_week_hours}시간을 넘게 일하기로 정해도 주휴시간은 하루 ${d.full_day_hours}시간까지예요.`);
      if (wage < d.min_wage_2026) notes.push(`2026년 최저임금(시급 ${u.won(d.min_wage_2026)})보다 낮은 시급이에요.`);
      notes.push('같은 일을 하는 통상근로자가 주 5일·40시간 일하는 사업장 기준이에요. 한 달 평균은 한 주 주휴수당 × 365 ÷ 7 ÷ 12로 계산했고, 원 미만은 버렸어요.');
      return { values: { holiday_hours: Math.round(holidayHours * 100) / 100, pay, monthly }, rows, notes };
    },
  });
})();
