/* 해외직구 관부가세 계산기: 물품가격(국제운송료 뺀 값)이 면세 기준(미화 150달러, 미국발 목록통관 물품은 200달러) 이하면 면세,
   넘으면 과세가격(물품가격 + 국제운송료·보험료) 전체에 관세(품목별 관세율)와 부가세 10% ((과세가격 + 관세) × 10%).
   관세법 제30조·제40조, 시행령 제37조, 시행규칙 제45조②, 특송물품 수입통관 사무처리에 관한 고시 제9조, 부가가치세법 제27조·제29조. 값은 data/direct-purchase-tax.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'direct-purchase-tax',
    title: '해외직구 관부가세 계산기',
    intro: '물건값·국제배송료·환율·관세율을 넣으면 면세인지, 관세와 부가세가 얼마인지 나와요.',
    inputs: [
      {
        name: 'route', label: '어떻게 들어오나요?', type: 'choice', default: 'us_list',
        options: [
          ['us_list', '미국발 목록통관 (200달러)'],
          ['other', '그 밖의 경우 (150달러)'],
        ],
        help: '미국에서 특송으로 오는 일반 물건만 200달러 기준이에요. 미국발이라도 건강기능식품·의약품·식품처럼 목록통관이 안 되는 물건, 다른 나라에서 오는 물건은 150달러로 골라요. 주류·담배처럼 다른 세금이 더 붙는 물건, 도서처럼 수입 부가세가 면제되는 물건은 결과가 달라서 이 계산기로 계산하지 않아요.',
      },
      { name: 'price', label: '물품가격 (미화 달러)', type: 'number', min: 0, step: 0.01, help: '실제 결제한 물건값에 현지 세금, 현지 배송료(배송대행지까지)를 더한 값이에요. 같은 판매자에게 같은 날 산 물건을 나눠 받으면 합쳐서 넣어요.' },
      { name: 'freight', label: '국제배송료·보험료 (미화 달러)', type: 'number', min: 0, step: 0.01, help: '한국까지 오는 배송료예요. 면세 판단에는 빼지만, 과세될 때는 세금 계산에 들어가요.' },
      { name: 'rate', label: '환율 (1달러당 원)', type: 'number', min: 0, step: 0.01, help: '실제 세금은 관세청이 주마다 정하는 과세환율로 계산해요.' },
      { name: 'duty_pct', label: '관세율 (%)', type: 'number', min: 0, step: 0.1, help: '물건마다 달라요. 관세청 해외직구물품 예상세액 조회에서 품목을 골라 확인해요.' },
    ],

    compute(v, d, u) {
      const price = Number(v.price);
      const freightRaw = v.freight === undefined || v.freight === null || v.freight === '' ? 0 : Number(v.freight);
      const rate = Number(v.rate);
      const dutyPct = Number(v.duty_pct);
      if (!(price > 0)) throw new u.InputError('물품가격(달러)을 넣으면 결과가 나와요.');
      if (price > d.max_usd) throw new u.InputError('물품가격이 너무 커요. 2,000달러를 넘으면 일반 수입신고 대상이라 관세사와 확인해요.');
      if (!(freightRaw >= 0) || freightRaw > d.max_usd) throw new u.InputError('국제배송료는 0 이상으로 넣어 주세요.');
      if (!(rate > 0) || rate > 10000) throw new u.InputError('환율(1달러당 원)을 넣어 주세요.');
      if (v.duty_pct === undefined || v.duty_pct === null || v.duty_pct === '' || !(dutyPct >= 0) || dutyPct > 100) throw new u.InputError('관세율(%)을 0~100 사이로 넣어 주세요.');
      const limit = d.exempt_usd[v.route];
      if (limit === undefined) throw new u.InputError('들어오는 경로를 골라 주세요.');
      const f = u.floor;
      const usd = (n) => `${n.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}달러`;
      // 센트 단위 정수로 비교해 소수 오차를 없앰
      const priceC = Math.round(price * 100);
      const freightC = Math.round(freightRaw * 100);
      const exempt = priceC <= limit * 100;
      const rows = [['면세 판단 금액 (물품가격)', `${usd(priceC / 100)} / 기준 ${usd(limit)}`]];
      const notes = [];
      if (exempt) {
        rows.push(['관세·부가세', '0원 (면세 기준 이하)']);
        notes.push(`물품가격이 ${limit}달러 이하라 관세와 부가세를 내지 않아요.`);
        notes.push('같은 판매자에게 같은 날 산 물건을 나눠 받거나 한 운송장으로 온 물건을 나눠 통관하면 합쳐서 판단해요.');
        return { values: { exempt: true, taxable: 0, duty: 0, vat: 0, total: 0 }, rows, notes };
      }
      const taxableC = priceC + freightC;
      const taxable = f((taxableC * rate) / 100);
      const dutyCalc = f((taxable * dutyPct) / 100 / 10) * 10;
      const dutyWaived = dutyCalc > 0 && dutyCalc < d.duty_min;
      const duty = dutyWaived ? 0 : dutyCalc;
      const vat = f(((taxable + dutyCalc) * d.vat_pct) / 100 / 10) * 10;
      const total = duty + vat;
      rows.push([`과세가격 (${usd(taxableC / 100)} × ${rate.toLocaleString('ko-KR')}원)`, u.won(taxable)]);
      rows.push([`관세 (과세가격 × ${dutyPct}%)`, dutyWaived ? `0원 (계산액 ${u.won(dutyCalc)}, 1만 원 미만이라 걷지 않음)` : u.won(duty)]);
      rows.push([`부가세 ((과세가격 + 관세) × ${d.vat_pct}%)`, u.won(vat)]);
      rows.push(['낼 세금 합계', u.won(total)]);
      rows.push(['과세가격 대비', `${(Math.round((total / taxable) * 10000) / 100).toFixed(2)}%`]);
      notes.push(`물품가격이 ${limit}달러를 넘어서 넘은 부분만이 아니라 국제배송료까지 더한 전체 금액에 세금이 붙어요.`);
      if (dutyWaived) notes.push('관세가 1만 원 미만이면 관세는 걷지 않아요. 부가세까지 빠지는지는 세관 안내로 확인해요.');
      notes.push('세금은 10원 미만을 버렸어요. 실제 고지액은 그 주 과세환율, 품목 분류, 운임 계산 방법에 따라 달라질 수 있어요.');
      return { values: { exempt: false, taxable, duty, vat, total }, rows, notes };
    },
  });
})();
