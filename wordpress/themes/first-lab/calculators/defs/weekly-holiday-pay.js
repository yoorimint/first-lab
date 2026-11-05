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
      { name: 'wage', label: '시급 (원)', type: 'number', min: 1, step: 1, default: 10320, help: '최저임금은 2026년 시급 10,320원, 2027년 10,700원이에요.' },
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
      // 빈칸은 Number('') = 0 이라 '15시간 미만이라 없음'으로 잘못 나왔음 (11/5 검수) → 안내 문장
      if (String(v.hours ?? '').trim() === '') throw new u.InputError('1주 소정근로시간을 넣어 주세요.');
      if (!(hours >= 0) || hours > d.max_input_hours) throw new u.InputError(`1주 소정근로시간은 0~${d.max_input_hours}시간으로 넣어 주세요.`);
      const notes = [];
      let reason = '';
      if (hours < d.min_weekly_hours) reason = `1주 소정근로시간이 ${d.min_weekly_hours}시간 미만이라 주휴수당이 없어요 (4주 평균 기준).`;
      else if (v.attended !== 'yes') reason = '소정근로일에 결근한 날이 있는 주는 그 주 주휴수당이 없어요.';
      const eligibleHours = hours >= d.min_weekly_hours ? Math.min(hours, d.full_week_hours) : 0; // 개근했을 때 기준
      // 정수로 곱한 뒤 마지막에 나눔: (23 ÷ 40 × 8) × 10,320 = 47,471.99… 를 버리면 1원이 빠짐 (11/5 검수)
      const centi = Math.round(eligibleHours * 100);
      const fullPay = FirstLabCalc.util.floor((centi * d.full_day_hours * wage) / (d.full_week_hours * 100));
      const holidayHours = v.attended === 'yes' ? (centi * d.full_day_hours) / (d.full_week_hours * 100) : 0;
      const pay = v.attended === 'yes' ? fullPay : 0;
      const monthly = FirstLabCalc.util.floor((fullPay * 365) / 84); // 매주 개근할 때 (결근한 주만 빠짐)
      const rows = [
        ['주휴시간', holidayHours ? `${hoursText(holidayHours)} (${hoursText(Math.min(hours, d.full_week_hours))} ÷ ${d.full_week_hours} × ${d.full_day_hours})` : '0시간'],
        ['한 주 주휴수당', u.won(pay)],
        ['한 달 평균 주휴수당 (매주 개근할 때)', u.won(monthly)],
      ];
      if (reason) notes.push(reason);
      if (hours > d.full_week_hours) notes.push(`${d.full_week_hours}시간을 넘게 일하기로 정해도 주휴시간은 하루 ${d.full_day_hours}시간까지예요.`);
      const year = Number(u.todayIso().slice(0, 4)) >= 2027 ? 2027 : 2026; // 2027.1.1 부터 2027년 최저임금
      const minWage = year === 2027 ? d.min_wage_2027 : d.min_wage_2026;
      if (wage < minWage) notes.push(`${year}년 최저임금(시급 ${u.won(minWage)})보다 낮은 시급이에요.`);
      notes.push('같은 일을 하는 통상근로자가 주 5일·40시간 일하는 사업장 기준이에요. 한 달 평균은 한 주 주휴수당 × 365 ÷ 7 ÷ 12로 계산했고, 원 미만은 버렸어요.');
      return { values: { holiday_hours: Math.round(holidayHours * 100) / 100, pay, monthly }, rows, notes };
    },
  });
})();
