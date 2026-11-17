/* 산재 장해급여 계산기: 장해등급 + 1일 평균임금 → 장해보상일시금·장해보상연금 (산재보험법 제57조, 별표 2 장해급여표).
   평균임금은 최고·최저 보상기준 금액 안으로 (법 제36조⑦). 제1~3급은 연금만 (시행령 제53조⑤), 제8~14급은 일시금만.
   금액은 data/disability-benefit.json */
(function () {
  'use strict';

  const GRADES = Array.from({ length: 14 }, (_, i) => [String(i + 1), `제${i + 1}급`]);

  FirstLabCalc.register({
    id: 'disability-benefit',
    title: '산재 장해급여 계산기',
    intro: '장해등급과 1일 평균임금을 넣으면 장해보상일시금과 장해보상연금을 계산해요.',
    inputs: [
      { name: 'grade', label: '장해등급', type: 'choice', default: '10', options: GRADES, help: '근로복지공단이 정한 장해등급이에요. 아직 모르면 예상 등급을 골라 보세요.' },
      { name: 'avg_wage', label: '1일 평균임금 (원)', type: 'number', min: 0, step: 1, default: 100000, help: '다치기 전 3개월 임금 합계를 그 기간 날수로 나눈 금액이에요. 공단 결정서에 적힌 평균임금이 있으면 그 값을 넣어요.' },
    ],

    compute(v, d, u) {
      const grade = Number(v.grade);
      const g = d.grades[String(grade)];
      if (!g) throw new u.InputError('장해등급을 골라 주세요.');
      if (String(v.avg_wage ?? '').trim() === '') throw new u.InputError('1일 평균임금을 넣어 주세요.');
      const wage = Number(v.avg_wage);
      if (!(wage > 0)) throw new u.InputError('1일 평균임금을 0보다 크게 넣어 주세요.');
      const floor = u.floor;
      const base = Math.min(Math.max(floor(wage), d.min_comp), d.max_comp);
      const pensionOnly = grade <= d.pension_only_max_grade;
      const lump = pensionOnly ? 0 : floor(base * g.lump_days);
      const pension = g.pension_days ? floor(base * g.pension_days) : 0;
      const monthly = pension ? floor(pension / 12) : 0;

      const rows = [
        ['장해등급', `제${grade}급`],
        [`계산에 쓰는 평균임금 (${u.won(d.min_comp)}~${u.won(d.max_comp)})`, u.won(base)],
      ];
      if (!pensionOnly) rows.push([`장해보상일시금 (${g.lump_days}일분)`, u.won(lump)]);
      if (pension) {
        rows.push([`장해보상연금 1년 치 (${g.pension_days}일분)`, u.won(pension)]);
        rows.push(['연금 한 달 치 (1년 치 ÷ 12)', u.won(monthly)]);
      }

      const notes = [`${d.year}년 최고 보상기준 금액 하루 ${u.won(d.max_comp)}, 최저 보상기준 금액 하루 ${u.won(d.min_comp)}을 넣었어요. 원 미만은 버렸어요.`];
      if (base !== floor(wage)) notes.push('평균임금이 보상기준 금액 범위 밖이라 그 범위의 끝 금액으로 계산했어요.');
      let years = 0;
      if (pensionOnly) {
        notes.push('제1~3급은 일시금 없이 연금으로만 받아요.');
      } else if (pension) {
        years = Math.round((g.lump_days / g.pension_days) * 10) / 10;
        notes.push(`제4~7급은 연금과 일시금 중 하나를 골라요. 평균임금이 그대로라면 연금을 약 ${years}년 받으면 일시금과 같아져요.`);
      } else {
        notes.push('제8~14급은 일시금으로만 받아요.');
      }
      if (pension) notes.push('연금은 1년 치를 12번에 나눠 매달 25일에 받아요. 평균임금은 해마다 오르내릴 수 있어 실제 연금액은 달라질 수 있어요.');
      notes.push('같은 부위에 이미 장해가 있었다면 늘어난 만큼만 받아요. 기존 장해 등급의 일수를 빼는데, 연금이고 기존 장해가 제8~14급이면 그 일시금 일수의 22.2%를 빼요.');
      notes.push('장해급여를 청구할 사유가 생긴 당시 대한민국 국민이 아니면서 외국에 사는 근로자는 등급과 관계없이 일시금으로 받아요. 실제 등급과 금액은 근로복지공단이 정해요.');
      return { values: { grade, base, lump, pension, monthly, years }, rows, notes };
    },
  });
})();
