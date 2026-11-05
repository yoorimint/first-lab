/* 실업급여(구직급여) 계산기: 1일 구직급여일액(평균임금 60%, 상한·하한) × 소정급여일수(별표 1). 금액·표는 data/unemployment-benefit.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'unemployment-benefit',
    title: '실업급여 계산기',
    intro: '퇴사 전 3개월 임금과 나이, 고용보험 가입 기간을 넣으면 하루 실업급여와 받을 수 있는 날수, 총액을 계산해요.',
    inputs: [
      { name: 'wage_3m', label: '퇴사 전 3개월 임금 합계 (세전, 원)', type: 'number', min: 0, step: 1, default: 9000000, help: '기본급·수당·상여금 등 세금 떼기 전 금액이에요. 3개월 동안 받은 임금을 모두 더해요.' },
      { name: 'days_3m', label: '그 3개월의 달력 일수', type: 'number', min: 89, max: 92, step: 1, default: 92, help: '퇴사일 전날부터 거꾸로 3개월의 날수예요. 보통 89~92일이에요.' },
      { name: 'hours', label: '하루 소정근로시간 (시간)', type: 'number', min: 1, max: 8, step: 0.1, default: 8, help: '주 40시간 근무면 8시간이에요. 주 단위로 정해졌다면 주 소정근로시간 ÷ 48 × 8 (예: 주 15시간 → 2.5) 처럼 넣어요. 하한액을 정할 때 써요.' },
      { name: 'age50', label: '퇴사일 기준 나이', type: 'select', default: 'under50', options: [['under50', '50세 미만'], ['over50', '50세 이상 (장애인 포함)']] },
      { name: 'period', label: '고용보험 가입 기간 (이번 회사)', type: 'select', default: '1', options: [['0', '1년 미만'], ['1', '1년 이상 3년 미만'], ['2', '3년 이상 5년 미만'], ['3', '5년 이상 10년 미만'], ['4', '10년 이상']] },
    ],

    compute(v, d, u) {
      const wage = Number(v.wage_3m);
      const days = Number(v.days_3m);
      const hours = Number(v.hours);
      if (!(wage >= 0)) throw new u.InputError('3개월 임금은 0 이상으로 넣어 주세요.');
      if (!(days >= 89 && days <= 92)) throw new u.InputError('3개월 일수는 89~92일 사이로 넣어 주세요.');
      if (!(hours >= 1 && hours <= d.max_hours)) throw new u.InputError('하루 소정근로시간은 1~8시간으로 넣어 주세요.');
      const table = d.days_table[v.age50 === 'over50' ? 'over50' : 'under50'];
      const idx = Number(v.period);
      if (!(idx >= 0 && idx < table.length)) throw new u.InputError('고용보험 가입 기간을 골라 주세요.');

      // 기초일액 = 평균임금 (3개월 임금 ÷ 일수), 상한 data.daily_wage_cap
      const avg = FirstLabCalc.util.floor(wage / days);
      const base = Math.min(avg, d.daily_wage_cap);
      // 하한 = 최저임금 × 소정근로시간 × 80%
      const minDaily = FirstLabCalc.util.floor(d.min_wage_hour * hours * d.min_rate);
      const calc = FirstLabCalc.util.floor(base * d.benefit_rate);
      const daily = Math.max(calc, minDaily);
      const benefitDays = table[idx];
      const total = daily * benefitDays;

      let basis = '평균임금 × 60%';
      if (daily === minDaily && calc < minDaily) basis = '하한액 적용 (최저임금 × 소정근로시간 × 80%)';
      else if (avg > d.daily_wage_cap) basis = `상한액 적용 (${u.won(d.daily_wage_cap)} × 60%)`;

      const rows = [
        ['1일 평균임금 (3개월 임금 ÷ 일수)', u.won(avg)],
        ['평균임금 × 60%', u.won(calc)],
        ['하한액 (이 근로시간 기준)', u.won(minDaily)],
        [`하루 실업급여 (${basis})`, u.won(daily)],
        [`받을 수 있는 날수 (${d.period_labels[idx]})`, `${benefitDays}일`],
        ['총액 (하루 금액 × 날수)', u.won(total)],
      ];
      const notes = [
        '원 미만은 버렸어요. 평균임금이 통상임금보다 적으면 통상임금으로 계산하기 때문에 실제 금액이 조금 더 많을 수 있어요.',
        '수급 조건(18개월 동안 피보험 단위기간 180일 이상, 비자발적 이직 등)을 채워야 받을 수 있어요.',
        '퇴사 다음 날부터 12개월이 지나면 남은 날수는 받지 못해요.',
        `하한액은 퇴사한 해의 최저임금으로 정해요. 이 계산은 오늘 기준 최저임금(시급 ${u.won(d.min_wage_hour)})을 썼어요.`,
      ];
      return {
        values: { avg_wage: avg, calc_daily: calc, min_daily: minDaily, daily, days: benefitDays, total },
        rows,
        notes,
      };
    },
  });
})();
