/* 월급 실수령액 계산기: 세전 월급 − 4대보험(근로자 몫) − 소득세(근로소득 간이세액표) − 지방소득세. 요율·간이세액표는 data/take-home-pay.json */
(function () {
  'use strict';

  // 간이세액표 해당란 세액 (소득세법 시행령 별표 2). taxable: 비과세 뺀 월급(원), fam: 공제대상가족 수(본인 포함, 1~11)
  function tableTax(taxable, fam, d) {
    const k = fam - 1;
    const thousand = taxable / 1000;
    if (thousand < d.table_floor_thousand) return 0;
    if (thousand < d.table_top_thousand) {
      // 행은 [이상, 미만, 1명 … 11명] (천원)
      const row = d.table.find((r) => thousand >= r[0] && thousand < r[1]);
      return row ? row[2 + k] : 0;
    }
    const base = d.table_top[k];
    if (taxable === d.table_top_thousand * 1000) return base;
    const b = d.over_top.find((o) => taxable > o.from && (o.to === null || taxable <= o.to));
    return base + b.add + Math.floor(((taxable - b.from) * b.factor_pct * b.rate_pct) / 10000);
  }

  // 별표 2 제4호: 11명 초과 = 11명 세액 − (10명 세액 − 11명 세액) × 초과 인원
  function familyTax(taxable, fam, d) {
    if (fam <= 11) return tableTax(taxable, fam, d);
    const t11 = tableTax(taxable, 11, d);
    const t10 = tableTax(taxable, 10, d);
    return Math.max(0, t11 - (t10 - t11) * (fam - 11));
  }

  // 별표 2 제3호: 8세 이상 20세 이하 자녀 수별 공제
  function childCredit(n, d) {
    if (n <= 0) return 0;
    if (n === 1) return d.child_credit[0];
    return d.child_credit[1] + d.child_credit_extra * (n - 2);
  }

  FirstLabCalc.register({
    id: 'take-home-pay',
    title: '월급 실수령액 계산기',
    intro: '세전 월급과 비과세 금액, 공제대상가족 수를 넣으면 4대보험과 소득세를 빼고 통장에 들어오는 금액을 계산해요.',
    inputs: [
      { name: 'pay', label: '세전 월급 (원)', type: 'number', min: 0, step: 1, default: 3000000, help: '기본급·수당을 더한 한 달 총액이에요. 연봉이면 12로 나눠서 넣어요.' },
      { name: 'nontax', label: '비과세 금액 (원)', type: 'number', min: 0, step: 1, default: 200000, help: '식대(월 20만 원까지) 같은 비과세 수당 합계예요. 없으면 0을 넣어요.' },
      { name: 'family', label: '공제대상가족 수 (본인 포함)', type: 'number', min: 1, step: 1, default: 1, help: '본인과 기본공제를 받는 배우자·부양가족을 각각 1명으로 세요.' },
      { name: 'children', label: '그중 8세 이상 20세 이하 자녀 수', type: 'number', min: 0, step: 1, default: 0 },
      {
        name: 'ratio', label: '소득세 원천징수 비율', type: 'select', default: '100',
        options: [['100', '100% (기본)'], ['80', '80% (신청한 경우)'], ['120', '120% (신청한 경우)']],
      },
      {
        name: 'pension', label: '국민연금', type: 'select', default: 'yes',
        options: [['yes', '낸다'], ['no', '안 낸다 (만 60세 이상 등)']],
      },
    ],

    compute(v, d, u) {
      const pay = Number(v.pay);
      const nontax = Number(v.nontax) || 0;
      const fam = Number(v.family);
      const children = Number(v.children) || 0;
      const ratio = Number(v.ratio || 100);
      if (!(pay > 0)) throw new u.InputError('세전 월급을 넣어 주세요.');
      if (nontax < 0 || nontax > pay) throw new u.InputError('비과세 금액은 0원부터 월급 이하로 넣어 주세요.');
      if (!Number.isInteger(fam) || fam < 1) throw new u.InputError('공제대상가족 수는 본인을 포함해 1명 이상으로 넣어 주세요.');
      if (!Number.isInteger(children) || children < 0 || children > fam - 1) throw new u.InputError('자녀 수는 공제대상가족 수에서 본인을 뺀 수까지만 넣을 수 있어요.');
      if (![80, 100, 120].includes(ratio)) throw new u.InputError('원천징수 비율을 골라 주세요.');

      const taxable = pay - nontax; // 4대보험·소득세 모두 비과세를 뺀 금액 기준

      // 국민연금: 기준소득월액(천원 미만 버림, 하한~상한) × 4.75%
      let pensionBase = 0;
      let pension = 0;
      if (v.pension !== 'no') {
        pensionBase = Math.min(Math.max(Math.floor(taxable / 1000) * 1000, d.pension_min), d.pension_max);
        pension = Math.floor((pensionBase * d.pension_rate_employee_per_10000) / 10000);
      }
      // 건강보험: 보수월액 × 7.19% 의 절반
      const health = Math.floor((taxable * d.health_rate_per_10000) / 20000);
      // 장기요양: 건강보험료 × (장기요양보험료율 ÷ 건강보험료율, 소수점 이하 다섯째자리에서 반올림) — 노인장기요양보험법 제9조① (2026.5.26 개정, 2026년 11월분부터)
      const ltcRatio = Math.round((d.ltc_rate_per_1000000 * 100) / d.health_rate_per_10000); // 0.9448/7.19 = 0.131404… → 0.1314 (1만분의 1314)
      const ltc = Math.floor((health * ltcRatio) / 10000);
      // 고용보험(실업급여): 보수 × 0.9%
      const employment = Math.floor((taxable * d.employment_rate_employee_per_1000) / 1000);
      const insurance = pension + health + ltc + employment;

      // 소득세: 간이세액표 − 자녀 공제(음수면 0) → 80·100·120%
      const tableAmount = familyTax(taxable, fam, d);
      const credit = childCredit(children, d);
      const afterChild = Math.max(0, tableAmount - credit);
      const incomeTax = Math.floor((afterChild * ratio) / 100);
      const localTax = Math.floor(incomeTax / 10);
      const tax = incomeTax + localTax;

      const net = pay - insurance - tax;

      const rows = [
        ['세전 월급', u.won(pay)],
        ['비과세 (빼고 계산)', u.won(nontax)],
        [v.pension === 'no' ? '국민연금 (안 냄)' : `국민연금 (${u.won(pensionBase)} × 4.75%)`, u.won(pension)],
        ['건강보험 (× 3.595%)', u.won(health)],
        [`장기요양보험 (건강보험료 × ${(ltcRatio / 100).toFixed(2)}%)`, u.won(ltc)],
        ['고용보험 (× 0.9%)', u.won(employment)],
        [ratio === 100 ? '소득세 (간이세액표)' : `소득세 (간이세액표 × ${ratio}%)`, u.won(incomeTax)],
        ['지방소득세 (소득세 × 10%)', u.won(localTax)],
        ['공제 합계', u.won(insurance + tax)],
        ['실수령액', u.won(net)],
      ];
      const notes = ['원 미만은 버렸어요. 회사 급여명세서와는 끝자리 처리 때문에 몇십 원 다를 수 있어요.'];
      if (credit > 0) notes.push(`8세 이상 20세 이하 자녀 ${children}명 공제 ${u.won(credit)}을 간이세액표 세액에서 뺐어요.`);
      if (v.pension !== 'no' && pensionBase !== Math.floor(taxable / 1000) * 1000) {
        notes.push(`국민연금은 기준소득월액 ${u.won(d.pension_min)}~${u.won(d.pension_max)} 범위로 계산해요.`);
      }
      notes.push('국민연금·건강보험은 실제로는 회사가 신고한 기준 금액으로 매겨져서, 이번 달 월급으로 계산한 값과 다를 수 있어요.');
      notes.push('65세 이후 새로 고용된 사람은 고용보험 실업급여 보험료를 내지 않아요. 상여가 나오는 달은 계산 방식이 달라요.');
      return {
        values: { taxable, pension_base: pensionBase, pension, health, ltc, employment, table_tax: tableAmount, income_tax: incomeTax, local_tax: localTax, net },
        rows,
        notes,
      };
    },
  });
})();
