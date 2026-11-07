/* 직장가입자 건강보험료 계산기: 보수월액보험료(회사와 반반) + 보수 외 소득월액보험료(본인 전액) + 장기요양보험료. 요율·상한·하한은 data/health-insurance.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'health-insurance',
    title: '직장인 건강보험료 계산기',
    intro: '월급(비과세 뺀 금액)을 넣으면 내가 내는 건강보험료·장기요양보험료와 회사가 내는 몫을 계산해요. 월급 말고 소득이 연 2,000만 원을 넘으면 추가로 내는 보험료도 같이 볼 수 있어요.',
    inputs: [
      { name: 'pay', label: '월 보수 (원, 비과세 제외)', type: 'number', min: 0, step: 1, default: 3000000, help: '기본급·수당을 더한 한 달 금액에서 식대 같은 비과세를 뺀 금액이에요.' },
      { name: 'interest', label: '월급 외 이자·배당소득 (1년, 원)', type: 'number', min: 0, step: 1, default: 0, help: '이자와 배당을 합쳐 1년에 1,000만 원 이하면 계산에 넣지 않아요.' },
      { name: 'income_full', label: '월급 외 사업·기타소득 (1년, 원)', type: 'number', min: 0, step: 1, default: 0, help: '임대·부업 사업소득은 매출이 아니라 필요경비를 뺀 소득금액이에요.' },
      { name: 'income_half', label: '월급 외 근로·연금소득 (1년, 원)', type: 'number', min: 0, step: 1, default: 0, help: '보수월액에 들어가지 않은 근로소득이나 연금소득이에요. 절반만 반영돼요.' },
    ],

    compute(v, d, u) {
      const floor10 = (n) => FirstLabCalc.util.floor(n / 10) * 10;
      const pay = Number(v.pay);
      const interest = Number(v.interest) || 0;
      const full = Number(v.income_full) || 0;
      const half = Number(v.income_half) || 0;
      if (!(pay > 0)) throw new u.InputError('월 보수를 넣어 주세요.');
      if ([interest, full, half].some((n) => n < 0)) throw new u.InputError('소득은 0 이상으로 넣어 주세요.');

      // 보수월액보험료: 보수월액 × 보험료율, 근로자·회사 50%씩 (국민건강보험법 제69조④·제76조①), 근로자 몫 상한·하한 (보건복지부고시) — 실수령액 계산기와 같은 식
      const health = Math.min(Math.max(floor10((pay * d.health_rate_per_10000) / 20000), d.health_employee_min), d.health_employee_max);
      // 장기요양: 건강보험료 × (장기요양보험료율 ÷ 건강보험료율, 소수점 이하 다섯째자리에서 반올림) — 노인장기요양보험법 제9조①
      const ltcRatio = Math.round((d.ltc_rate_per_1000000 * 100) / d.health_rate_per_10000);
      const ltc = FirstLabCalc.util.floor((health * ltcRatio) / 10000);
      const employee = health + ltc;

      // 보수 외 소득월액보험료 (국민건강보험법 제71조①, 시행령 제41조④ 연 2천만 원, 시행규칙 제44조: 이자·배당 합 1천만 원 이하 제외, 근로·연금 50%)
      const interestCounted = interest > d.interest_exclude_max ? interest : 0;
      const fullAll = interestCounted + full;
      const otherTotal = fullAll + half;
      let extraMonth = 0;
      let extra = 0;
      let extraLtc = 0;
      if (otherTotal > d.other_income_deduction) {
        const excessMonth = (otherTotal - d.other_income_deduction) / 12;
        extraMonth = FirstLabCalc.util.floor((excessMonth * (fullAll + half * d.half_rate_income)) / otherTotal);
        extra = Math.min(floor10((extraMonth * d.health_rate_per_10000) / 10000), d.other_income_premium_max);
        extraLtc = FirstLabCalc.util.floor((extra * ltcRatio) / 10000);
      }
      const myTotal = employee + extra + extraLtc;

      const rows = [
        [`건강보험료 (월 보수 × ${d.health_rate_per_10000 / 100}% 의 절반)`, u.won(health)],
        [`장기요양보험료 (건강보험료 × ${(ltcRatio / 100).toFixed(2)}%)`, u.won(ltc)],
        ['월급에서 빠지는 금액 (본인 몫)', u.won(employee)],
        ['회사가 내는 몫 (같은 금액)', u.won(employee)],
      ];
      if (otherTotal > 0) {
        rows.push(['월급 외 소득 합계 (반영 대상)', u.won(otherTotal)]);
        rows.push(['보수 외 소득월액 (2,000만 원 넘는 부분 ÷ 12, 평가 후)', u.won(extraMonth)]);
        rows.push(['추가 건강보험료 (본인 전액)', u.won(extra)]);
        rows.push(['추가 장기요양보험료', u.won(extraLtc)]);
        rows.push(['내가 내는 한 달 합계', u.won(myTotal)]);
      }
      const notes = ['건강보험료는 10원 미만, 장기요양보험료는 원 미만을 버렸어요. 급여명세서와 끝자리가 몇 원 다를 수 있어요.', '휴직자·섬 지역 등 보험료 경감, 사립학교 교원의 부담 비율(본인 50%·학교 30%·국가 20%)은 반영하지 않았어요.'];
      if (health === d.health_employee_max) notes.push(`본인 몫 건강보험료는 한 달 ${u.won(d.health_employee_max)}을 넘지 않아요.`);
      if (health === d.health_employee_min) notes.push(`본인 몫 건강보험료는 하한 ${u.won(d.health_employee_min)}으로 매겨요 (경감 대상이면 이보다 적을 수 있어요).`);
      if (interest > 0 && interestCounted === 0) notes.push('이자·배당소득이 1년 1,000만 원 이하라 계산에서 뺐어요.');
      if (otherTotal > 0 && otherTotal <= d.other_income_deduction) notes.push('월급 외 소득이 1년 2,000만 원 이하라 추가 보험료가 없어요.');
      if (extra > 0) notes.push('추가 보험료는 회사와 나누지 않고 본인이 전부 내요. 1~10월은 전전년도, 11~12월은 전년도 소득 자료로 매겨요 (연금소득은 1~10월도 전년도 자료).');
      return {
        values: { health, ltc, employee, extra_month: extraMonth, extra, extra_ltc: extraLtc, my_total: myTotal },
        rows,
        notes,
      };
    },
  });
})();
