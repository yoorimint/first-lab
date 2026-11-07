/* 종부세 계산기 (주택분, 개인): 공시가격 합계 − 공제(9억·12억) → ×60% 과세표준 → 세율 → 재산세 공제 → 1세대 1주택 세액공제 → 농어촌특별세 20%. 값은 data/property-holding-tax.json */
(function () {
  'use strict';

  const KIND = [
    ['one', '1세대 1주택자 (공제 12억 원)'],
    ['joint', '부부 공동명의 1주택 (각자 지분, 공제 9억 원)'],
    ['two', '2주택 이하 (공제 9억 원)'],
    ['three', '3주택 이상 (공제 9억 원)'],
  ];

  // 누진 구간마다 천분율을 곱해 더한다 (법 제9조 표와 같은 값)
  function progressive(base, brackets) {
    let tax = 0;
    let lo = 0;
    for (const b of brackets) {
      const hi = b.upto === null ? base : Math.min(base, b.upto);
      if (hi > lo) tax += ((hi - lo) * b.permille) / 1000;
      if (b.upto === null || base <= b.upto) break;
      lo = b.upto;
    }
    return FirstLabCalc.util.floor(tax);
  }

  const pick = (table, n) => (table.find((t) => n >= t.from) || { pct: 0 }).pct;

  FirstLabCalc.register({
    id: 'property-holding-tax',
    title: '종부세 계산기 (주택분)',
    intro: '주택 공시가격 합계와 주택 수를 넣으면 과세표준, 재산세 공제, 1주택자 세액공제, 농어촌특별세까지 계산해요.',
    inputs: [
      { name: 'price', label: '주택 공시가격 합계 (원)', type: 'number', min: 0, step: 10000000, help: '6월 1일 기준 내 명의 주택의 공시가격을 모두 더해요. 부부 공동명의 1주택은 기본이면 내 지분만큼, 특례(1세대 1주택자 계산)를 신청했다면 집 전체 공시가격을 1세대 1주택자로 넣어요.' },
      { name: 'kind', label: '주택 수', type: 'select', default: 'one', options: KIND },
      { name: 'age', label: '만 나이 (1세대 1주택자만)', type: 'number', min: 0, step: 1, default: 0, help: '6월 1일 기준 만 나이. 60세 미만이면 0 그대로 둬요.' },
      { name: 'years', label: '보유 기간 (년, 1세대 1주택자만)', type: 'number', min: 0, step: 1, default: 0, help: '6월 1일까지 그 집을 가진 햇수예요.' },
    ],

    compute(v, d, u) {
      const price = Number(v.price);
      const kind = v.kind;
      const age = Number(v.age) || 0;
      const years = Number(v.years) || 0;
      if (!(price > 0)) throw new u.InputError('공시가격 합계를 넣으면 결과가 나와요.');
      if (!(kind in d.deduction)) throw new u.InputError('주택 수를 골라 주세요.');
      if (age < 0 || years < 0) throw new u.InputError('나이와 보유 기간은 0 이상으로 넣어 주세요.');

      const deduction = d.deduction[kind];
      const base = FirstLabCalc.util.floor((Math.max(0, price - deduction) * d.fair_ratio_pct) / 100);
      const tax = progressive(base, d.brackets[kind === 'three' ? 'three' : 'two']);
      // 재산세 공정시장가액비율: 공동명의여도 재산세는 1세대 1주택(지방세법 시행령 제109조①2호) → 45%
      const ratio = kind === 'one' || kind === 'joint' ? d.property_tax.ratio_pct_one : d.property_tax.ratio_pct_other;
      const propertyCredit = Math.min(tax, FirstLabCalc.util.floor((base * ratio * d.property_tax.top_rate_permille) / 100000));
      const calcTax = tax - propertyCredit;
      let creditPct = 0;
      if (kind === 'one') creditPct = Math.min(d.credit_max_pct, pick(d.age_credit, age) + pick(d.hold_credit, years));
      const credit = FirstLabCalc.util.floor((calcTax * creditPct) / 100);
      // 낼 종부세·농특세는 10원 미만 버림 (국고금관리법 제47조①)
      const jongbu = FirstLabCalc.util.floor10(calcTax - credit);
      const rural = FirstLabCalc.util.floor10((jongbu * d.rural_pct) / 100);
      const total = jongbu + rural;

      const rows = [
        ['공제 금액', u.won(deduction)],
        ['과세표준 (공제 후 × 60%)', u.won(base)],
        ['세율 적용 세액', u.won(tax)],
        ['재산세 중복분 공제', `-${u.won(propertyCredit)}`],
      ];
      if (kind === 'one') rows.push([`고령자·장기보유 공제 (${creditPct}%)`, `-${u.won(credit)}`]);
      rows.push(['종합부동산세', u.won(jongbu)]);
      rows.push(['농어촌특별세 (20%)', u.won(rural)]);
      rows.push(['합계', u.won(total)]);

      const notes = [
        '재산세 중복분 공제는 올해 재산세가 표준세율 그대로 나왔다고 보고 계산했어요. 재산세 과세표준 상한이 적용됐다면 고지서 금액과 조금 다를 수 있어요.',
        '작년보다 세금(재산세+종부세)이 1.5배를 넘으면 넘는 부분은 안 내요. 이 세부담 상한은 작년 세액이 있어야 해서 계산에 넣지 않았어요.',
      ];
      if (base === 0) notes.push('공시가격 합계가 공제 금액 이하라서 종부세 대상이 아니에요.');
      if (jongbu > d.installment_over) notes.push('종부세가 250만 원을 넘으면 일부를 납부기한 뒤 6개월 안에 나눠 낼 수 있어요.');
      return {
        values: { deduction, base, tax, property_credit: propertyCredit, calc_tax: calcTax, credit_pct: creditPct, credit, jongbu, rural, total },
        rows,
        notes,
      };
    },
  });
})();
