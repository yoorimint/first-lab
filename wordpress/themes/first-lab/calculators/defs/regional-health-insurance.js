/* 지역가입자 건강보험료 계산기: 소득 보험료 + 재산 보험료 + 장기요양보험료. 요율·점수표는 data/regional-health-insurance.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'regional-health-insurance',
    title: '지역가입자 건강보험료 계산기',
    intro: '1년 소득과 재산세 과세표준, 전월세 보증금을 넣으면 한 달 건강보험료와 장기요양보험료를 계산해요.',
    inputs: [
      { name: 'income_full', label: '사업·이자·배당·기타소득 (1년, 원)', type: 'number', min: 0, step: 1, default: 0, help: '사업소득은 매출이 아니라 필요경비를 뺀 소득금액이에요. 이자·배당소득은 합쳐서 1,000만 원 이하면 넣지 않아요.' },
      { name: 'income_half', label: '근로·연금소득 (1년, 원)', type: 'number', min: 0, step: 1, default: 0, help: '절반만 반영돼요. 근로소득은 총급여, 공적연금은 1년 동안 받은 연금 전액을 넣어요.' },
      { name: 'tax_base', label: '재산세 과세표준 합계 (원)', type: 'number', min: 0, step: 1, default: 0, help: '세대가 가진 주택·토지·건물의 재산세 과세표준을 더해서 넣어요. 시세가 아니에요.' },
      { name: 'deposit', label: '전월세 보증금 (원, 무주택 세대만)', type: 'number', min: 0, step: 1, default: 0 },
      { name: 'rent', label: '월세 (원, 무주택 세대만)', type: 'number', min: 0, step: 1, default: 0 },
    ],

    compute(v, d, u) {
      const full = Number(v.income_full) || 0;
      const half = Number(v.income_half) || 0;
      const taxBase = Number(v.tax_base) || 0;
      const deposit = Number(v.deposit) || 0;
      const rent = Number(v.rent) || 0;
      if ([full, half, taxBase, deposit, rent].some((n) => n < 0)) throw new u.InputError('금액은 0 이상으로 넣어 주세요.');

      // 소득: (전액 반영 소득 + 근로·연금 50%) ÷ 12, 28만 원 이하면 하한액 적용
      const yearly = full + half * d.half_rate_income;
      const incomeMonth = Math.round(yearly / 12);
      const low = yearly / 12 <= d.low_income_month;
      const incomePremium = low ? d.monthly_min : FirstLabCalc.util.floor((yearly * d.rate_per_10000) / 120000);

      // 재산: 과세표준 + (보증금 + 월세×40)×30% - 1억 원 → 등급 점수 × 점수당 금액
      const lease = (deposit + rent * d.lease_rent_multiplier) * d.lease_ratio;
      const base = taxBase + lease - d.property_deduction;
      let points = 0;
      if (base > 0) {
        const manwon = base / 10000;
        const hit = d.property_grades_manwon.find(([limit]) => manwon <= limit);
        points = hit ? hit[1] : d.property_top_points;
      }
      const propertyPremium = FirstLabCalc.util.floor(points * d.point_value);

      const sum = incomePremium + propertyPremium;
      const capped = sum > d.monthly_max;
      // 상한·하한은 소득+재산 합계에 적용 (국민건강보험법 제69조⑤⑥, 시행령 제32조 2호 나목) — 11/5 외부 검수: 하한을 소득 보험료에만 적용했던 오류
      const health = Math.min(Math.max(sum, d.monthly_min), d.monthly_max);
      // 장기요양: 건강보험료 × 장기요양보험료율 ÷ 건강보험료율. 2026.11.27 부터는 그 비율을 소수점 이하 다섯째자리에서 반올림
      // (노인장기요양보험법 제9조①, 2026.5.26 개정 — data 의 ltc_ratio_round 가 그날 scheduled 로 켜짐)
      const ltcRatio = (d.ltc_rate_per_1000000 * 100) / d.rate_per_10000; // 1만분의 단위
      const ltc = FirstLabCalc.util.floor((health * (d.ltc_ratio_round ? Math.round(ltcRatio) : ltcRatio)) / 10000);
      const total = health + ltc;

      const rows = [
        ['소득월액 (평가 후 1년 소득 ÷ 12)', u.won(incomeMonth)],
        [low ? '소득 보험료 (월 28만 원 이하 → 최저 금액)' : `소득 보험료 (소득월액 × ${d.rate_per_10000 / 100}%)`, u.won(incomePremium)],
        ['재산 반영 금액 (1억 원 공제 후)', base > 0 ? u.won(base) : '0원'],
        ['재산 점수', `${points.toLocaleString('ko-KR')}점`],
        [`재산 보험료 (점수 × ${d.point_value}원)`, u.won(propertyPremium)],
        ['건강보험료', u.won(health)],
        [`장기요양보험료 (건강보험료 × ${(ltcRatio / 100).toFixed(2)}%)`, u.won(ltc)],
        ['한 달 합계', u.won(total)],
      ];
      const notes = ['원 미만은 버렸어요. 고지서는 끝자리 처리 때문에 몇십 원 다를 수 있어요.'];
      if (capped) notes.push(`건강보험료는 한 달 ${u.won(d.monthly_max)}을 넘지 않아요.`);
      if (base <= 0 && (taxBase > 0 || lease > 0)) notes.push('재산이 1억 원 공제 안쪽이라 재산 점수를 0으로 계산했어요.');
      notes.push('65세 이상·농어촌 경감, 주택 구입·전세 대출 공제는 넣지 않았어요.');
      return {
        values: { income_month: incomeMonth, income_premium: incomePremium, points, property_premium: propertyPremium, health, ltc, total },
        rows,
        notes,
      };
    },
  });
})();
