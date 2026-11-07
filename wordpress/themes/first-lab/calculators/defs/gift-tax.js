/* 증여세 계산기 (아파트 등 기본세율 증여): 시가 − 증여재산공제 → 과세표준 → 세율 → 세대생략 할증 → 신고세액공제. 값은 data/gift-tax.json */
(function () {
  'use strict';

  const RELATION = [
    ['parent_adult', '부모(직계존속) → 성년 자녀'],
    ['parent_minor', '부모(직계존속) → 미성년 자녀'],
    ['spouse', '배우자'],
    ['grandparent_adult', '조부모 → 성년 손자녀 (할증)'],
    ['grandparent_minor', '조부모 → 미성년 손자녀 (할증)'],
    ['child', '자녀(직계비속) → 부모'],
    ['relative', '4촌 이내 혈족·3촌 이내 인척'],
    ['other', '그 밖의 사람 (5촌 이상 친척 포함)'],
  ];
  const ASCENDANT = ['parent_adult', 'parent_minor', 'grandparent_adult', 'grandparent_minor'];

  function baseTax(base, brackets) {
    const b = brackets.find((x) => x.upto === null || base <= x.upto);
    return { tax: Math.max(0, FirstLabCalc.util.floor((base * b.rate_pct) / 100) - b.deduction), rate: b.rate_pct };
  }

  FirstLabCalc.register({
    id: 'gift-tax',
    title: '아파트 증여세 계산기',
    intro: '아파트 시가와 누구에게 받는지 고르면 증여재산공제, 과세표준, 세율, 신고하면 낼 증여세를 계산해요.',
    inputs: [
      { name: 'price', label: '아파트 시가 (원)', type: 'number', min: 0, step: 1000000, help: '증여일 전 6개월~후 3개월 사이 같은 단지·비슷한 면적의 매매가나 감정가를 넣어요.' },
      { name: 'relation', label: '누구에게 받나요', type: 'select', default: 'parent_adult', options: RELATION },
      { name: 'used', label: '10년 안에 같은 쪽에서 이미 쓴 공제 (원)', type: 'number', min: 0, step: 1000000, default: 0, help: '예: 3년 전 부모님께 현금 3,000만 원을 받아 공제받았다면 30000000' },
      { name: 'marriage', label: '혼인·출산 공제로 뺄 금액 (원, 최대 1억)', type: 'number', min: 0, step: 1000000, default: 0, help: '부모·조부모에게 혼인신고일 전후 2년, 출생일부터 2년 안에 받을 때만 넣어요.' },
      { name: 'on_time', label: '기한 안에 신고하나요', type: 'select', default: 'yes', options: [['yes', '네 (신고세액공제 3%)'], ['no', '아니요']] },
    ],

    compute(v, d, u) {
      const price = Number(v.price);
      const used = Number(v.used) || 0;
      const marriage = Number(v.marriage) || 0;
      const limit = d.deductions[v.relation];
      if (!(price > 0)) throw new u.InputError('아파트 시가를 넣으면 결과가 나와요.');
      if (limit === undefined) throw new u.InputError('누구에게 받는지 골라 주세요.');
      if (used < 0 || marriage < 0) throw new u.InputError('공제 금액은 0 이상으로 넣어 주세요.');
      if (marriage > d.marriage_birth_max) throw new u.InputError(`혼인·출산 공제는 ${u.won(d.marriage_birth_max)}까지예요.`);
      if (marriage > 0 && !ASCENDANT.includes(v.relation)) throw new u.InputError('혼인·출산 공제는 부모·조부모에게 받을 때만 돼요.');

      const basic = Math.min(Math.max(0, limit - used), price);
      const extra = Math.min(marriage, price - basic);
      const base = price - basic - extra;
      const raw = baseTax(base, d.brackets);
      const rate = raw.rate;
      const tax = base < d.min_taxable_base ? 0 : raw.tax; // 과세표준 50만 원 미만이면 부과하지 않음 (제55조②)
      let surchargePct = 0;
      if (v.relation.startsWith('grandparent')) {
        surchargePct = v.relation === 'grandparent_minor' && price > d.skip_generation.minor_over_amount
          ? d.skip_generation.minor_over_pct : d.skip_generation.rate_pct;
      }
      const surcharge = FirstLabCalc.util.floor((tax * surchargePct) / 100);
      const credit = v.on_time === 'no' ? 0 : FirstLabCalc.util.floor(((tax + surcharge) * d.filing_credit_pct) / 100);
      // 낼 세액은 10원 미만 버림 (국고금관리법 제47조①)
      const pay = FirstLabCalc.util.floor10(tax + surcharge - credit);

      const rows = [
        ['증여재산공제', u.won(basic)],
        ['혼인·출산 공제', u.won(extra)],
        ['과세표준', u.won(base)],
        ['적용 세율', `${rate}%`],
        ['산출세액', u.won(tax)],
      ];
      if (surchargePct) rows.push([`세대생략 할증 (${surchargePct}%)`, u.won(surcharge)]);
      rows.push(['신고세액공제 (3%)', `-${u.won(credit)}`]);
      rows.push(['낼 증여세', u.won(pay)]);
      const notes = [
        '10년 안에 같은 사람(부모라면 그 배우자 포함)에게 받은 재산은 합쳐서 계산하고 그때 낸 세금은 빼 줘요. 이 계산기는 이번 아파트만 넣었어요.',
        '증여받은 사람이 국내 거주자일 때 기준이에요. 증여세와 별도로 취득세를 내야 해요.',
      ];
      if (surchargePct) notes.push('부모가 먼저 사망해 손자녀가 받는 경우에는 할증이 붙지 않아요.');
      if (v.on_time === 'no') notes.push('기한을 넘기면 신고세액공제를 못 받고, 무신고 가산세(낼 세액의 20%)가 붙어요.');
      return {
        values: { basic, extra, base, rate, tax, surcharge, credit, pay },
        rows,
        notes,
      };
    },
  });
})();
