/* 부가가치세 계산기: 공급가액↔합계 나누기, 일반과세자·간이과세자 납부세액. 요율은 data/vat.json */
(function () {
  'use strict';

  const INDUSTRY = [
    ['retail', '소매업·음식점업·재생용 재료수집 및 판매업 (15%)'],
    ['manufacture', '제조업·농업·임업·어업·소화물 전문 운송업 (20%)'],
    ['lodging', '숙박업 (25%)'],
    ['construction', '건설업·운수 및 창고업·정보통신업 (30%)'],
    ['finance', '금융·보험, 전문·과학·기술서비스, 사업시설관리·임대서비스, 부동산 관련·임대업 (40%)'],
    ['other_service', '그 밖의 서비스업 (30%)'],
  ];

  const pct = (r) => `${Math.round(r * 1000) / 10}%`;

  FirstLabCalc.register({
    id: 'vat',
    title: '부가가치세 계산기',
    intro: '공급가액에 부가세를 더하거나, 부가세 포함 금액을 공급가액과 세액으로 나눠요. 일반과세자·간이과세자 납부세액도 계산해요.',
    inputs: [
      {
        name: 'mode', label: '무엇을 계산할까요', type: 'select', default: 'add',
        options: [
          ['add', '공급가액 → 부가세·합계 (10% 더하기)'],
          ['split', '합계(부가세 포함) → 공급가액·부가세 나누기'],
          ['general', '일반과세자 납부세액 (매출세액 - 매입세액)'],
          ['simple', '간이과세자 납부세액 (업종별 부가가치율)'],
        ],
      },
      { name: 'amount', label: '금액 (원)', type: 'number', min: 0, step: 1, help: '더하기는 공급가액, 나누기는 합계금액, 일반과세자는 매출 공급가액, 간이과세자는 1년 매출(부가세 포함 공급대가)을 넣어요.' },
      { name: 'purchase', label: '매입 금액 (원, 납부세액 계산용)', type: 'number', min: 0, step: 1, default: 0, help: '일반과세자는 세금계산서 등에 적힌 매입세액, 간이과세자는 세금계산서 등으로 받은 매입 공급대가(부가세 포함)를 넣어요.' },
      { name: 'industry', label: '업종 (간이과세자만)', type: 'select', default: 'retail', options: INDUSTRY },
    ],

    compute(v, d, u) {
      const amount = Number(v.amount);
      const purchase = Number(v.purchase) || 0;
      if (!(amount > 0)) throw new u.InputError('금액을 넣으면 결과가 나와요.');
      if (!Number.isInteger(amount) || !Number.isInteger(purchase)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (purchase < 0) throw new u.InputError('매입 금액은 0 이상으로 넣어 주세요.');
      const notes = [];
      let values;
      let rows;

      if (v.mode === 'add') {
        const tax = FirstLabCalc.util.floor(amount * d.rate);
        values = { supply: amount, tax, total: amount + tax };
        rows = [['공급가액', u.won(amount)], ['부가세 (10%)', u.won(tax)], ['합계', u.won(amount + tax)]];
        notes.push('원 미만은 버렸어요.');
      } else if (v.mode === 'split') {
        const supply = Math.round((amount * 10) / (10 + Math.round(d.rate * 100) / 10));
        const tax = amount - supply;
        values = { supply, tax, total: amount };
        rows = [['합계 (부가세 포함)', u.won(amount)], ['공급가액 (합계 ÷ 1.1)', u.won(supply)], ['부가세 (합계 - 공급가액)', u.won(tax)]];
        notes.push('공급가액은 원 미만을 반올림했어요. 영수증마다 끝자리 처리 방식이 달라 1원 차이가 날 수 있어요.');
      } else if (v.mode === 'general') {
        const sales = FirstLabCalc.util.floor(amount * d.rate);
        const pay = sales - purchase;
        values = { sales_tax: sales, purchase_tax: purchase, pay };
        rows = [['매출세액 (공급가액 × 10%)', u.won(sales)], ['매입세액', u.won(purchase)]];
        rows.push(pay >= 0 ? ['낼 세액', u.won(pay)] : ['돌려받을 세액 (환급)', u.won(-pay)]);
        notes.push('신용카드·현금영수증 발행 세액공제, 가산세, 예정고지로 미리 낸 세액은 넣지 않았어요.');
      } else if (v.mode === 'simple') {
        const ratio = d.simple.value_added_rates[v.industry];
        if (ratio === undefined) throw new u.InputError('업종을 골라 주세요.');
        const gross = FirstLabCalc.util.floor((amount * Math.round(ratio * 100) * Math.round(d.rate * 100)) / 10000);
        const credit = FirstLabCalc.util.floor(purchase * d.simple.purchase_credit_rate);
        const exempt = amount < d.simple.exempt_below;
        const pay = exempt ? 0 : Math.max(0, gross - credit);
        values = { gross, credit, pay, exempt };
        rows = [
          ['1년 매출 (공급대가)', u.won(amount)],
          ['업종별 부가가치율', pct(ratio)],
          ['납부세액 (매출 × 부가가치율 × 10%)', u.won(gross)],
          ['매입 공제 (매입 공급대가 × 0.5%)', `-${u.won(credit)}`],
          ['낼 세액', exempt ? '0원 (납부의무 면제)' : u.won(pay)],
        ];
        notes.push('간이과세자는 공제액이 납부세액보다 많아도 환급되지 않아요.');
        notes.push(`1년 매출(공급대가)이 ${u.won(d.simple.exempt_below)} 미만이면 납부의무가 면제돼요.`);
        notes.push('신용카드·현금영수증 발행 세액공제와 전자세금계산서 발급 공제는 넣지 않았어요.');
        if (amount >= d.simple.threshold) notes.push(`한 해(1~12월) 공급대가가 ${u.won(d.simple.threshold)} 이상이면 다음 해 7월 1일부터 일반과세자가 돼요 (부동산임대업·과세유흥장소는 4,800만 원 기준, 부가가치세법 제61조·제62조).`);
      } else {
        throw new u.InputError('계산할 항목을 골라 주세요.');
      }
      return { values, rows, notes };
    },
  });
})();
