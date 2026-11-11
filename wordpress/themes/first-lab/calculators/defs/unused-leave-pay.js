/* 미사용 연차수당 계산기: 1일 통상임금 × 쓰지 못한 연차 일수. 기준값은 data/unused-leave-pay.json
 * 근거: 근로기준법 제18조③(4주 평균 1주 소정근로시간 15시간 미만은 제60조 미적용), 제60조⑤(휴가 기간 임금 = 취업규칙 등에서 정하는 통상임금 또는 평균임금)·⑦(1년 안에 안 쓰면 소멸, 사용자 귀책이면 예외)·제61조(사용 촉진 → 보상 의무 없음),
 * 제11조·시행령 제7조 별표 1(상시 4명 이하 사업장은 제60조 미적용), 시행령 제6조②③(시급 = 월급 ÷ 월 통상임금 산정 기준시간 수, 주 40시간이면 209시간 — 고용노동부; 일급 = 시급 × 1일 소정근로시간).
 * 고용노동부 해석(근로개선정책과-4218, 2013.7.19): 취업규칙 등에 정함이 없으면 통상임금, 휴가청구권이 있는 마지막 달의 통상임금으로 지급.
 * 수당 = 버리기 전 1일 통상임금 × 일수, 원 미만 버림. 화면의 1일 통상임금은 원 미만 버림. */
(function () {
  'use strict';

  const num = (x) => Math.round(x * 100) / 100;

  FirstLabCalc.register({
    id: 'unused-leave-pay',
    title: '미사용 연차수당 계산기',
    intro: '통상임금과 쓰지 못한 연차 일수를 넣으면 받을 연차수당이 나와요.',
    inputs: [
      {
        name: 'pay_type', label: '임금 형태', type: 'select', default: 'monthly',
        options: [['monthly', '월급 (월 통상임금)'], ['hourly', '시급']],
      },
      { name: 'amount', label: '월 통상임금 또는 시급 (원)', type: 'number', min: 1, step: 1, default: 2508000, help: '연차를 쓸 수 있던 마지막 달 기준이에요. 기본급에 정기적·일률적으로 주는 수당을 더하고, 연장·야간·휴일근로수당은 빼요.' },
      { name: 'base_hours', label: '월 통상임금 산정 기준시간 (월급일 때)', type: 'number', min: 1, step: 'any', default: 209, help: '주 40시간·주휴 8시간이면 209시간이에요. 단시간이면 (1주 소정근로시간 + 주휴시간) × 365 ÷ 7 ÷ 12.' },
      { name: 'day_hours', label: '1일 소정근로시간 (시간)', type: 'number', min: 0.5, step: 0.5, default: 8, help: '근로계약서에 정한 하루 근로시간이에요. 8시간을 넘게 정했어도 법정 근로시간은 하루 8시간이에요.' },
      { name: 'week_hours', label: '1주 소정근로시간 (시간)', type: 'number', min: 0, step: 0.5, default: 40, help: '근로계약서에 정한 1주 근로시간이에요 (연장근로는 빼고, 주마다 다르면 4주 평균). 15시간 미만이면 연차가 생기지 않아요.' },
      { name: 'days', label: '쓰지 못한 연차 일수 (일)', type: 'number', min: 0, step: 0.5, default: 7 },
      { name: 'employees', label: '상시 근로자 수 (명)', type: 'number', min: 1, step: 1, default: 5, help: '4명 이하 사업장은 연차 유급휴가 규정이 적용되지 않아요.' },
      {
        name: 'promoted', label: '회사가 법에서 정한 서면 사용 촉진을 했나요', type: 'select', default: 'no',
        options: [['no', '아니요 (또는 모름)'], ['yes', '네, 서면 촉구와 사용 시기 지정 통보를 모두 받았어요']],
      },
    ],

    compute(v, d, u) {
      const amount = Number(v.amount);
      const baseHours = Number(v.base_hours);
      const dayHours = Number(v.day_hours);
      const days = Number(v.days);
      const employees = Number(v.employees);
      const weekHours = Number(v.week_hours);
      if (!(amount > 0) || !Number.isInteger(amount)) throw new u.InputError('통상임금(또는 시급)을 1원 단위로 넣어 주세요.');
      if (v.pay_type === 'monthly' && !(baseHours > 0 && baseHours <= 300)) throw new u.InputError('월 통상임금 산정 기준시간을 1~300시간으로 넣어 주세요.');
      if (!(dayHours >= 0.5 && dayHours <= d.max_day_hours)) throw new u.InputError(`1일 소정근로시간을 0.5~${d.max_day_hours}시간으로 넣어 주세요.`);
      if (String(v.days ?? '').trim() === '' || !(days >= 0) || days > d.max_days || Math.round(days * 2) !== days * 2) throw new u.InputError(`쓰지 못한 연차 일수를 0~${d.max_days}일, 0.5일 단위로 넣어 주세요.`);
      if (!(employees >= 1) || !Number.isInteger(employees)) throw new u.InputError('상시 근로자 수를 1명 이상으로 넣어 주세요.');
      // 빈칸은 Number('') = 0 이라 '15시간 미만'으로 잘못 나옴 → 안내 문장
      if (String(v.week_hours ?? '').trim() === '' || !(weekHours >= 0) || weekHours > d.max_week_hours) throw new u.InputError(`1주 소정근로시간을 0~${d.max_week_hours}시간으로 넣어 주세요.`);

      const hours = Math.min(dayHours, d.legal_day_hours);
      const enoughHours = weekHours >= d.min_week_hours;
      const covered = employees >= d.min_employees && enoughHours;
      const promoted = v.promoted === 'yes';
      // 월급은 정수 곱을 먼저 하고 마지막에 나눔 (소수 오차로 1원 빠지는 것 막기)
      const hourly = v.pay_type === 'hourly' ? amount : amount / baseHours;
      const dayWageExact = v.pay_type === 'hourly' ? amount * hours : (amount * hours) / baseHours;
      const dayWage = FirstLabCalc.util.floor(dayWageExact);
      const full = v.pay_type === 'hourly'
        ? FirstLabCalc.util.floor(amount * hours * days)
        : FirstLabCalc.util.floor((amount * hours * days) / baseHours);
      const pay = covered && !promoted ? full : 0;

      const rows = [
        ['통상시급', v.pay_type === 'hourly' ? u.won(amount) : `${num(hourly).toLocaleString('ko-KR')}원 (${u.won(amount)} ÷ ${num(baseHours)}시간)`],
        ['1일 통상임금', `${u.won(dayWage)} (통상시급 × ${num(hours)}시간)`],
        ['미사용 연차수당', covered && !promoted ? `${u.won(pay)} (1일 통상임금 × ${num(days)}일)` : '0원 (아래 안내 참고)'],
      ];
      const notes = [];
      if (!enoughHours) notes.push(`4주 평균 1주 소정근로시간이 ${d.min_week_hours}시간 미만인 근로자는 연차 유급휴가 규정이 적용되지 않아 법으로 정한 연차수당이 없어요.`);
      if (employees < d.min_employees) notes.push('상시 4명 이하 사업장은 연차 유급휴가 규정이 적용되지 않아 법으로 정한 연차수당이 없어요. 근로계약서·취업규칙에 연차를 정했다면 그 기준을 따라요.');
      if (covered && promoted) notes.push(`회사가 법에서 정한 사용 촉진을 모두 했는데도 쓰지 않아 사용 기간이 끝나 없어진 연차는 보상 의무가 없어요. 1차 촉구가 없거나, 필요한 2차 지정 통보가 빠졌다면 ${u.won(full)}을 받을 수 있어요. 지정된 휴가일에 출근해 일했는데 회사가 일을 받지 않겠다는 뜻을 분명히 밝히지 않았거나 업무를 시켰다면 보상 의무가 남아요(대법원 2019다279283).`);
      if (dayHours > d.legal_day_hours) notes.push(`1일 소정근로시간은 법정 근로시간인 하루 ${d.legal_day_hours}시간까지로 계산했어요.`);
      notes.push('취업규칙·단체협약에서 평균임금으로 주기로 정했다면 금액이 달라져요. 정함이 없으면 통상임금으로 계산해요.');
      notes.push('1일 통상임금은 원 미만을 버려 보여 주고, 수당은 버리기 전 금액에 일수를 곱한 뒤 원 미만을 버렸어요.');
      return { values: { hourly: num(hourly), day_wage: dayWage, pay, full }, rows, notes };
    },
  });
})();
