/* 배당금 계산기: 보유 주식 수 × 1주당 배당금 → 세전 배당금, 배당소득세(14%)·지방소득세(소득세의 10%), 세후 배당금, 1년 합계, 배당수익률.
 * 세율은 data/dividend-tax.json (소득세법 제129조①2호나목 배당소득 원천징수 14%, 지방세법 제103조의13①, 10원 미만 버림 국고금관리법 제47조①, 1,000원 미만 소액 부징수 소득세법 제86조1호,
 * 소득세법 제14조③6호 이자·배당 2,000만 원 종합과세 기준). 국내 상장주식 일반 배당 기준 */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'dividend-tax',
    title: '배당금 계산기 (세후 배당금·배당수익률)',
    intro: '가진 주식 수와 1주당 배당금을 넣으면 한 번 받을 때와 1년 동안 받는 세전 배당금, 떼는 세금, 세후 배당금이 나와요. 주가를 넣으면 배당수익률도 나와요.',
    inputs: [
      { name: 'shares', label: '보유 주식 수 (주)', type: 'number', min: 0, step: 1 },
      { name: 'dps', label: '1주당 배당금 (원, 1회분)', type: 'number', min: 0, step: 1, help: '회사가 공시한 1주당 배당금이에요. 분기·월 배당이면 한 번에 주는 금액을 넣어요.' },
      { name: 'times', label: '1년 지급 횟수', type: 'select', default: '1', options: [['1', '연 1회 (결산배당)'], ['2', '연 2회 (반기배당)'], ['4', '연 4회 (분기배당)'], ['12', '연 12회 (월배당)']] },
      { name: 'price', label: '주가 (원, 선택)', type: 'number', min: 0, step: 1, help: '넣으면 1년 배당금 기준 배당수익률을 계산해요.' },
      { name: 'tax', label: '세금', type: 'select', default: 'normal', options: [['normal', '일반과세 15.4%'], ['none', '세금 없이 (비과세 계좌 등)']] },
    ],

    compute(v, d, u) {
      const f = u.floor;
      const shares = Number(v.shares);
      const dps = Number(v.dps);
      const times = Number(v.times || 1);
      if (!(shares > 0) || !Number.isInteger(shares)) throw new u.InputError('보유 주식 수를 1주 이상 정수로 넣어 주세요.');
      if (!(dps > 0) || !Number.isInteger(dps)) throw new u.InputError('1주당 배당금을 1원 이상 정수로 넣어 주세요.');
      if (![1, 2, 4, 12].includes(times)) throw new u.InputError('1년 지급 횟수를 골라 주세요.');
      const gross = shares * dps;
      if (gross * times > d.max_amount) throw new u.InputError('배당금이 너무 커요. 1년 1,000억 원 이하로 넣어 주세요.');

      let incomeTax = 0;
      let localTax = 0;
      if (v.tax !== 'none') {
        // 배당소득세 14%, 지방소득세는 그 10% — 받을 때마다 각각 10원 미만 버림
        incomeTax = f((gross * Math.round(d.income_tax_rate * 100)) / 100 / 10) * 10;
        localTax = f((incomeTax * Math.round(d.local_rate * 100)) / 100 / 10) * 10;
        // 소액 부징수: 배당소득 원천징수세액이 1,000원 미만이면 떼지 않음 (소득세법 제86조1호 — 이자소득만 제외), 지방소득세도 0
        if (incomeTax < d.min_withholding) { incomeTax = 0; localTax = 0; }
      }
      const tax = incomeTax + localTax;
      const net = gross - tax;
      const annualGross = gross * times;
      const annualTax = tax * times;
      const annualNet = net * times;

      const rows = [
        ['1회 세전 배당금', u.won(gross)],
        v.tax === 'none' ? ['1회 세금', '0원'] : ['1회 세금', `${u.won(tax)} (배당소득세 ${u.won(incomeTax)} + 지방소득세 ${u.won(localTax)})`],
        ['1회 세후 배당금 (입금액)', u.won(net)],
      ];
      if (times > 1) rows.push([`1년 세전 배당금 (${times}회)`, u.won(annualGross)], ['1년 세금', u.won(annualTax)], ['1년 세후 배당금', u.won(annualNet)]);

      const values = { gross, income_tax: incomeTax, local_tax: localTax, tax, net, annual_gross: annualGross, annual_tax: annualTax, annual_net: annualNet };
      const price = Number(v.price);
      if (v.price !== '' && v.price !== undefined && price > 0) {
        if (!Number.isInteger(price)) throw new u.InputError('주가는 1원 단위 정수로 넣어 주세요.');
        // 배당수익률(%) 소수 둘째 자리 반올림 — 정수(BigInt)로 계산 (소수 계산은 1.045% 같은 딱 반인 값을 1.04% 로 내림, 11/8 독립 검수)
        const pct2 = (num, den) => Number((2n * BigInt(num) * 10000n + BigInt(den)) / (2n * BigInt(den))) / 100;
        const yieldPct = pct2(dps * times, price);
        const netYield = pct2(net * times, price * shares);
        values.yield = yieldPct;
        values.net_yield = netYield;
        rows.push(['배당수익률 (세전)', `${yieldPct.toFixed(2)}%`], ['배당수익률 (세후)', `${netYield.toFixed(2)}%`]);
      }

      const notes = [
        '국내 상장주식의 현금배당을 기준으로, 받을 때마다 배당소득세 14%와 지방소득세(소득세의 10%)를 각각 10원 미만 버려 계산했어요. 1회 배당소득세가 1,000원 미만이면 세금을 떼지 않아요.',
        '배당금과 지급 횟수는 회사가 그때그때 정해요. 지난해와 같은 금액이 나온다는 보장은 없어요.',
      ];
      if (v.tax !== 'none') notes.push('해외주식 배당은 현지에서 먼저 세금을 떼서 이 계산과 달라요.');
      if (annualGross > d.comprehensive_threshold) {
        notes.push('1년 배당금만으로 2,000만 원을 넘어요. 이자와 배당을 합쳐 2,000만 원을 넘는 부분은 다음 해 5월 종합소득세 신고 때 다른 소득과 합쳐 다시 계산해서, 세금이 더 나올 수 있어요.');
        notes.push('요건을 갖춘 상장 고배당기업의 배당은 2028년이 속하는 사업연도분까지 합산배제를 신청하면 다른 소득과 합치지 않고 따로 과세(2,000만 원 이하 14%, 3억 원 이하 20% 등)할 수 있어요(조세특례제한법 제104조의27).');
      } else {
        notes.push('이자와 배당을 합쳐 한 해 2,000만 원 이하면 이렇게 떼고 끝나요. 넘으면 넘는 부분은 다음 해 5월 종합소득세 신고 때 다른 소득과 합쳐 다시 계산해요.');
      }
      return { values, rows, notes };
    },
  });
})();
