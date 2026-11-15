/* 주휴수당 계산기: 1주 소정근로시간·시급 → 주휴시간·주휴수당. 기준값은 data/weekly-holiday-pay.json
 * 근거: 근로기준법 제55조①·제18조③, 같은 법 시행령 제30조①·별표 2(단시간근로자 1일 소정근로시간 = 4주 소정근로시간 ÷ 통상근로자 4주 소정근로일 수)
 * 통상근로자가 주 5일·40시간인 사업장 기준으로 (1주 소정근로시간 ÷ 40) × 8시간, 8시간 상한. */
(function () {
  'use strict';

  const hoursText = (h) => `${Math.round(h * 100) / 100}시간`;

  FirstLabCalc.register({
    id: 'weekly-holiday-pay',
    title: '주휴수당 계산기',
    intro: '시급, 1주에 일하기로 정한 시간, 그 주에 있었던 일을 넣으면 받는지와 금액이 나와요.',
    inputs: [
      { name: 'wage', label: '시급 (원)', type: 'number', min: 1, step: 1, default: 'data:min_wage', help: '처음 값은 올해 최저시급이에요.' },
      { name: 'hours', label: '일주일에 일하기로 한 시간', type: 'number', min: 0, step: 0.5, default: 20, help: '계약서에 적힌 시간이에요. 더 일한 시간은 빼요.' },
      {
        name: 'came', label: '그 주에 일하기로 한 날, 다 나왔나요?', type: 'choice', default: 'yes',
        options: [['yes', '네, 다 나왔어요'], ['no', '아니요']],
        help: '지각·조퇴는 나온 거예요. 공휴일이나 가게 사정으로 쉰 날도 괜찮아요.',
      },
      {
        name: 'why', label: '못 나온 이유는요?', type: 'choice', default: 'sick', show: { came: ['no'] },
        options: [['sick', '아팠어요'], ['leave', '허락받고 쉬었어요'], ['absent', '그냥 빠졌어요']],
      },
      {
        name: 'rule', label: '회사 규칙에 따로 정해 뒀나요?', type: 'choice', default: 'unknown',
        show: { came: ['no'], why: ['sick', 'leave'] },
        options: [['yes', '네'], ['unknown', '없어요 / 몰라요']],
        help: '회사 규칙은 \'취업규칙\'이에요. 아팠으면 \'병가는 출근으로 본다\', 허락받고 쉬었으면 \'무급휴가\'가 적혀 있는지 보세요.',
      },
      {
        name: 'wage_type', label: '시급에 주휴수당이 들어 있다고 했나요?', type: 'choice', default: 'separate',
        options: [['separate', '아니요'], ['included', '네, 포함 시급이에요']],
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
      // 그 주에 있었던 일 → 개근으로 보나 (11/15 운영자 '판별기': 고용노동부 해석·상담 답변으로 확인한 경우만)
      const WEEK = {
        yes: [true, ''],
        sick_rule: [true, '회사 규칙에 병가를 출근으로 본다고 정했으면, 나머지 일하기로 한 날을 다 나온 주는 받아요 (고용노동부 근로개선정책과-3833, 2014).'],
        sick: [false, '병가라도 회사 규칙에 따로 정한 게 없으면 결근으로 봐서, 그 주는 주휴수당이 없어요 (고용노동부 근로개선정책과-3833, 2014).'],
        unpaid_rule: [true, '회사 규칙에 무급휴가가 있으면 그날을 빼고, 나머지 일하기로 한 날을 다 나온 주는 받아요 (고용노동부 상담 답변, 2026).'],
        unpaid: [false, '무급휴가 규칙이 없으면 결근으로 볼 수 있어, 그 주는 못 받을 수 있어요. 회사 규칙(취업규칙)을 확인해 보세요 (고용노동부 상담 답변, 2026).'],
        no: [false, '일하기로 한 날에 결근한 주는 그 주 주휴수당이 없어요.'],
      };
      // 단추 답(came·why·rule) → 그 주 사정 (예전 입력 attended 도 그대로 받음 — 검증 사례)
      const situation = v.attended || (v.came !== 'no' ? 'yes'
        : v.why === 'sick' ? (v.rule === 'yes' ? 'sick_rule' : 'sick')
          : v.why === 'leave' ? (v.rule === 'yes' ? 'unpaid_rule' : 'unpaid') : 'no');
      const week = WEEK[situation] || WEEK.no;
      const attended = week[0];
      let reason = '';
      if (hours < d.min_weekly_hours) reason = `1주 소정근로시간이 ${d.min_weekly_hours}시간 미만이라 주휴수당이 없어요 (4주 평균 기준).`;
      else if (!attended) reason = week[1];
      else if (week[1]) notes.push(week[1]);
      const eligibleHours = hours >= d.min_weekly_hours ? Math.min(hours, d.full_week_hours) : 0; // 개근했을 때 기준
      // 정수로 곱한 뒤 마지막에 나눔: (23 ÷ 40 × 8) × 10,320 = 47,471.99… 를 버리면 1원이 빠짐 (11/5 검수)
      const centi = Math.round(eligibleHours * 100);
      const fullPay = FirstLabCalc.util.floor((centi * d.full_day_hours * wage) / (d.full_week_hours * 100));
      const holidayHours = attended ? (centi * d.full_day_hours) / (d.full_week_hours * 100) : 0;
      const pay = attended ? fullPay : 0;
      const monthly = FirstLabCalc.util.floor((fullPay * 365) / 84); // 매주 개근할 때 (결근한 주만 빠짐)
      const verdict = pay > 0 ? '받아요' : hours >= d.min_weekly_hours && situation === 'unpaid' ? '못 받을 수 있어요' : '못 받아요';
      const rows = [
        ['주휴시간', holidayHours ? `${hoursText(holidayHours)} (${hoursText(Math.min(hours, d.full_week_hours))} ÷ ${d.full_week_hours} × ${d.full_day_hours})` : '0시간'],
        ['한 주 주휴수당', u.won(pay)],
      ];
      if (pay > 0) rows.push(['한 달 평균 (매주 받으면)', u.won(monthly)]);  // 못 받는 주에 한 달 금액이 보이면 헷갈림 (11/16)
      if (reason) notes.unshift(reason);
      // '주휴수당 포함 시급': 최저임금 + 주휴수당만큼 높아야 (고용노동부 근로개선정책과-2617, 2012 · 상담 답변 2025)
      let includedFloor = 0;
      if (v.wage_type === 'included' && eligibleHours > 0) {
        const hc = Math.round(hours * 100);   // 한 주 받는 돈(시급 × 계약 시간) ≥ 최저임금 × (계약 시간 + 주휴시간)
        includedFloor = Math.ceil((d.min_wage * (hc + (centi * d.full_day_hours) / d.full_week_hours)) / hc - 1e-9);
        notes.push(wage >= includedFloor
          ? `주휴수당 포함 시급으로 괜찮은 금액이에요. 이 계약 시간이면 ${u.won(includedFloor)} 이상이어야 해요 (최저임금 + 주휴수당).`
          : `주휴수당 포함 시급이라면 ${u.won(includedFloor)} 이상이어야 해요. 지금 시급은 그보다 낮아요 (최저임금 + 주휴수당).`);
      }
      if (hours > d.full_week_hours) notes.push(`${d.full_week_hours}시간을 넘게 일하기로 정해도 주휴시간은 하루 ${d.full_day_hours}시간까지예요.`);
      // 최저임금은 data 의 min_wage (해마다 scheduled 로 1월 1일 자동 전환)
      if (wage < d.min_wage && v.wage_type !== 'included') notes.push(`${d.min_wage_year}년 최저임금(시급 ${u.won(d.min_wage)})보다 낮은 시급이에요.`);
      notes.push(pay > 0 ? '같은 일을 하는 통상근로자가 주 5일·40시간 일하는 사업장 기준이에요. 한 달 평균은 한 주 주휴수당 × 365 ÷ 7 ÷ 12로 계산했고, 원 미만은 버렸어요.' : '같은 일을 하는 통상근로자가 주 5일·40시간 일하는 사업장 기준이에요.');
      return { headline: `그 주 주휴수당: ${verdict}${pay > 0 ? ` (${u.won(pay)})` : ''}`, values: { holiday_hours: Math.round(holidayHours * 100) / 100, pay, monthly, below_min_wage: wage < d.min_wage, eligible: pay > 0, included_floor: includedFloor }, rows, notes };
    },
  });
})();
