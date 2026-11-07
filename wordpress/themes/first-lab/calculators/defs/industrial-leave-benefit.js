/* 산재 휴업급여 계산기: 1일 평균임금 70%(산재법 제52조), 최고 보상기준 금액(제36조⑦), 저소득 특례(제54조), 61세 이후 감액(제55조·별표 1). 금액은 data/industrial-leave-benefit.json */
(function () {
  'use strict';

  const AGE_RATE = { 61: [66, 86], 62: [62, 82], 63: [58, 78], 64: [54, 74], 65: [50, 70] }; // 별표 1: [제1호 ×n/70 → 평균임금의 n%, 제2·3호 ×m/90]

  FirstLabCalc.register({
    id: 'industrial-leave-benefit',
    title: '산재 휴업급여 계산기',
    intro: '재해 전 3개월 임금과 쉰 날수를 넣으면 하루 휴업급여와 그 기간 합계를 계산해요.',
    inputs: [
      { name: 'wage_3m', label: '재해 전 3개월 임금 합계 (세전, 원)', type: 'number', min: 0, step: 1, default: 9000000, help: '재해가 난 날(진단 확정일) 전 3개월 동안 받은 기본급·수당 등 세금 떼기 전 금액이에요.' },
      { name: 'days_3m', label: '그 3개월의 달력 일수', type: 'number', min: 89, max: 92, step: 1, default: 92, help: '보통 89~92일이에요.' },
      { name: 'off_days', label: '요양으로 일하지 못한 날수', type: 'number', min: 0, step: 1, default: 30, help: '입원·통원 치료로 일을 못 한 날수예요.' },
      { name: 'age', label: '휴업급여를 받는 날의 나이', type: 'select', default: 'under61', options: [['under61', '61세 미만'], ['61', '61세'], ['62', '62세'], ['63', '63세'], ['64', '64세'], ['65', '65세 이상']] },
      { name: 'late_injury', label: '나이 감액을 하지 않는 경우인가요?', type: 'select', default: 'no', options: [['no', '아니요'], ['yes', '예 (요양 시작 2년 이내)']], help: '61세 이후 일하다 업무상 재해(부상·질병)로 요양을 시작했거나, 61세 전 업무상 질병으로 장해급여를 받고 61세 이후 그 질병으로 처음 요양한 경우예요. 요양을 시작한 날부터 2년 동안은 나이 감액을 하지 않아요.' },
    ],

    compute(v, d, u) {
      const wage = Number(v.wage_3m);
      const days = Number(v.days_3m);
      const off = Number(v.off_days);
      if (!(wage >= 0)) throw new u.InputError('3개월 임금은 0 이상으로 넣어 주세요.');
      if (!(days >= 89 && days <= 92)) throw new u.InputError('3개월 일수는 89~92일 사이로 넣어 주세요.');
      if (!(off >= 0 && Number.isInteger(off))) throw new u.InputError('일하지 못한 날수는 0 이상의 정수로 넣어 주세요.');
      const floor = FirstLabCalc.util.floor;

      const avg = floor(wage / days);
      const base = Math.min(avg, d.max_comp); // 최고 보상기준 금액까지만 (최저 보상기준 금액은 휴업급여에 적용 안 함, 제36조⑦ 단서)
      const line = floor(d.min_comp * d.low_line_rate); // 최저 보상기준 금액의 80%
      const minWageDay = d.min_wage_hour * 8; // 최저임금액 = 시간급 × 8
      const d70 = floor(base * d.rate);

      let daily;
      let rule;
      if (base * d.rate <= line) {
        const d90 = floor(base * d.low_rate);
        // 단서 판정은 버리기 전 90% 금액으로 (제54조① 단서 '100분의 90에 상당하는 금액이 … 많은 경우', 독립 검수 11/7: 평균임금 73,387원)
        if (base * d.low_rate > line) { daily = line; rule = 'cap'; }
        else if (d90 < minWageDay) { daily = minWageDay; rule = 'minwage'; }
        else { daily = d90; rule = 'low'; }
      } else { daily = d70; rule = 'basic'; }

      const ageKey = v.age === 'under61' ? null : Number(v.age);
      let reduced = false;
      if (ageKey && AGE_RATE[ageKey] && v.late_injury !== 'yes') {
        const [n, m] = AGE_RATE[ageKey];
        const third = floor(d.min_comp * d.low_line_rate * m / 90); // 별표 1 제3호
        if (rule === 'basic') daily = Math.max(floor(base * n / 100), third); // 제1호 (단서: 제3호보다 적으면 제3호)
        else if (rule === 'cap') daily = third;
        else daily = floor(daily * m / 90); // 제2호
        reduced = true;
      }
      const paid = off > d.no_pay_days;
      const total = paid ? daily * off : 0;

      const ruleText = {
        basic: '평균임금 × 70%',
        low: '저소득 특례: 평균임금 × 90%',
        cap: `저소득 특례 상한: 최저 보상기준 금액의 80% (${u.won(line)})`,
        minwage: `최저임금액 보장 (${u.won(minWageDay)})`,
      }[rule];
      const rows = [
        ['1일 평균임금 (3개월 임금 ÷ 일수)', u.won(avg)],
        [`계산에 쓰는 평균임금 (${u.won(d.max_comp)}까지)`, u.won(base)],
        ['평균임금 × 70%', u.won(d70)],
        [`하루 휴업급여 (${ruleText}${reduced ? ', 나이 감액' : ''})`, u.won(daily)],
        [`일하지 못한 ${off}일 합계`, paid ? u.won(total) : `0원 (${d.no_pay_days}일 이내라 지급 안 됨)`],
      ];
      const notes = [
        `${d.year}년 기준 금액이에요: 최고 보상기준 금액 하루 ${u.won(d.max_comp)}, 최저 보상기준 금액 하루 ${u.won(d.min_comp)}, 최저임금액 하루 ${u.won(minWageDay)}.`,
        '원 미만은 버렸어요. 평균임금이 통상임금보다 적으면 통상임금으로 계산하고, 실제 금액은 근로복지공단이 정해요.',
      ];
      if (rule === 'cap') notes.push('평균임금 × 90%가 최저 보상기준 금액의 80%를 넘어 그 80%로 계산했어요. 이 구간은 평균임금이 조금 낮은 사람(최저임금액 보장)보다 하루 금액이 적게 나올 수 있으니 공단 결정 통지의 산정 내역을 함께 보세요.');
      if (rule === 'minwage') notes.push('평균임금 × 90%가 최저임금액보다 적어 최저임금액을 하루 휴업급여로 계산했어요.');
      if (reduced) notes.push('61세부터는 나이에 따라 줄어든 금액이에요 (산업재해보상보험법 별표 1).');
      if (paid) notes.push('일하지 못한 기간이 4일 이상이라 그 기간 전체를 계산했어요.');
      else notes.push('일하지 못한 기간이 3일 이내면 산재 휴업급여는 나오지 않아요. 이 기간은 회사가 근로기준법에 따라 평균임금의 60%를 휴업보상으로 줘야 해요.');
      return {
        values: { avg_wage: avg, base, d70, daily, total },
        rows,
        notes,
      };
    },
  });
})();
