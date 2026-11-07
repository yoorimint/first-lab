/* 연차수당 계산기: 1일 통상임금 × 쓰지 못한 연차 일수. 기준값은 data/annual-leave-pay.json
 * 근거: 근로기준법 제60조⑤(연차 기간 임금 = 취업규칙 등에서 정하는 통상임금 또는 평균임금)·⑦(1년간 안 쓰면 소멸, 사용자 귀책은 예외), 제61조(사용 촉진),
 * 제11조·시행령 제7조 별표 1(상시 4명 이하 사업장은 제60조 미적용), 제18조③(4주 평균 주 15시간 미만은 제60조 미적용),
 * 시행령 제6조②·③(월급 ÷ 월 통상임금 산정 기준시간 수 = 시급, 시급 × 1일 소정근로시간 = 1일 통상임금 — 주 40시간이면 209시간, 고용노동부).
 * 고용노동부 행정해석(근로개선정책과-4218, 2013.7.19): 규정이 없으면 휴가청구권이 있는 마지막 달의 통상임금으로 지급.
 * 끝수: 1일 통상임금은 원 미만 버림으로 보여 주고, 수당은 중간 반올림 없이 월급 × 1일 시간 × 일수 ÷ 기준시간을 한 번에 계산해 원 미만 버림. */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'annual-leave-pay',
    title: '연차수당 계산기',
    intro: '월 통상임금(또는 시급)과 쓰지 못한 연차 일수를 넣으면 미사용 연차수당이 나와요.',
    inputs: [
      {
        name: 'pay_type', label: '임금 형태', type: 'select', default: 'monthly',
        options: [['monthly', '월급 (월 통상임금)'], ['hourly', '시급']],
      },
      { name: 'amount', label: '월 통상임금 또는 시급 (원)', type: 'number', min: 1, step: 1, default: 2500000, help: '기본급에 정기적·일률적으로 주는 고정 수당을 더한 금액이에요. 연장·야간·휴일근로수당은 빼요.' },
      { name: 'base_hours', label: '월 통상임금 산정 기준시간 (월급일 때)', type: 'number', min: 1, step: 'any', default: 209, help: '주 40시간·주휴 8시간이면 209시간이에요. 단시간이면 (1주 소정근로시간 + 주휴시간) × 365 ÷ 7 ÷ 12.' },
      { name: 'day_hours', label: '1일 소정근로시간 (시간)', type: 'number', min: 0.5, step: 0.5, default: 8, help: '근로계약서에 정한 하루 근로시간이에요 (연장근로 제외).' },
      { name: 'days', label: '쓰지 못한 연차 일수 (일)', type: 'number', min: 0, step: 0.5, default: 5, help: '반차를 썼다면 0.5일 단위로 넣어요.' },
      { name: 'employees', label: '상시 근로자 수 (명)', type: 'number', min: 1, step: 1, default: 5, help: '4명 이하 사업장은 법정 연차 규정이 적용되지 않아요.' },
    ],

    compute(v, d, u) {
      const amount = Number(v.amount);
      const baseHours = Number(v.base_hours);
      const dayHours = Number(v.day_hours);
      const employees = Number(v.employees);
      if (!(amount > 0) || !Number.isInteger(amount)) throw new u.InputError('통상임금(또는 시급)을 1원 단위로 넣어 주세요.');
      if (v.pay_type === 'monthly' && !(baseHours > 0 && baseHours <= d.max_base_hours)) throw new u.InputError(`월 통상임금 산정 기준시간을 1~${d.max_base_hours}시간으로 넣어 주세요.`);
      if (!(dayHours > 0 && dayHours <= d.max_day_hours)) throw new u.InputError(`1일 소정근로시간을 0.5~${d.max_day_hours}시간으로 넣어 주세요.`);
      // 빈칸은 Number('') = 0 이라 '0원'으로 잘못 나옴 (주휴수당 계산기 11/5 검수와 같은 꼴) → 안내 문장
      if (String(v.days ?? '').trim() === '') throw new u.InputError('쓰지 못한 연차 일수를 넣어 주세요.');
      const days = Number(v.days);
      if (!(days >= 0) || days > d.max_days || Math.round(days * 2) !== days * 2) throw new u.InputError(`연차 일수는 0~${d.max_days}일, 0.5일 단위로 넣어 주세요.`);
      if (!(employees >= 1) || !Number.isInteger(employees)) throw new u.InputError('상시 근로자 수를 1명 이상으로 넣어 주세요.');

      const monthly = v.pay_type !== 'hourly';
      const hourly = monthly ? amount / baseHours : amount;
      // 정수로 곱한 뒤 마지막에 나눔 (소수 오차로 1원 빠지지 않게)
      const halfDays = Math.round(days * 2);
      const dayCenti = Math.round(dayHours * 100);
      const daily = monthly ? FirstLabCalc.util.floor((amount * dayCenti) / (baseHours * 100)) : FirstLabCalc.util.floor((amount * dayCenti) / 100);
      const fullPay = monthly
        ? FirstLabCalc.util.floor((amount * dayCenti * halfDays) / (baseHours * 200))
        : FirstLabCalc.util.floor((amount * dayCenti * halfDays) / 200);
      const covered = employees >= d.min_employees;
      const pay = covered ? fullPay : 0;
      const notes = [];
      const rows = [
        ['통상시급', monthly ? `${(Math.round(hourly * 100) / 100).toLocaleString('ko-KR')}원 (${u.won(amount)} ÷ ${Math.round(baseHours * 100) / 100}시간, 소수 둘째 자리까지 표시)` : u.won(amount)],
        ['1일 통상임금', monthly ? `${u.won(daily)} (월 통상임금 × ${Math.round(dayHours * 100) / 100}시간 ÷ ${Math.round(baseHours * 100) / 100}시간, 원 미만 버림)` : `${u.won(daily)} (시급 × ${Math.round(dayHours * 100) / 100}시간)`],
        ['미사용 연차수당', covered ? `${u.won(pay)} (${days}일)` : '0원 (법정 연차 미적용)'],
      ];
      if (!covered) notes.push(`상시 근로자 ${d.min_employees - 1}명 이하 사업장은 근로기준법의 연차 규정이 적용되지 않아 법정 연차수당이 없어요. 취업규칙·근로계약에 연차를 정했다면 그 규정을 따라요.`);
      const hourlyFloor = FirstLabCalc.util.floor(hourly);
      if (hourlyFloor < d.min_wage) notes.push(`통상시급이 ${d.min_wage_year}년 최저임금(시급 ${u.won(d.min_wage)})보다 낮아요. 최저임금에 들어가는 임금 범위는 통상임금과 다를 수 있어 따로 확인해요.`);
      notes.push('취업규칙·단체협약에서 평균임금으로 주기로 정했다면 평균임금으로 계산해요. 정한 것이 없으면 통상임금, 휴가를 쓸 수 있던 마지막 달의 통상임금이 기준이에요.');
      notes.push('회사가 법에 정한 서면 사용 촉진(근로기준법 제61조)을 했는데도 쓰지 않아 없어진 연차는 수당이 없어요. 4주 평균 1주 소정근로시간이 15시간 미만이면 연차 자체가 없어요.');
      return { values: { daily, pay, covered }, rows, notes };
    },
  });
})();
