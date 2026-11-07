/* 기타소득세 계산기: 받은 금액 - 필요경비(60%·80% 또는 실제 경비 중 큰 것) = 기타소득금액, 건별 5만 원 이하면 과세최저한,
   넘으면 소득세 20% + 지방소득세(소득세의 10%). 요율은 data/other-income-tax.json (소득세법 제37조·제84조·제129조, 시행령 제87조) */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'other-income-tax',
    title: '기타소득세 계산기',
    intro: '한 번 받은 강연료·원고료·상금 같은 기타소득을 넣으면 떼는 소득세·지방소득세와 실제로 받는 금액이 나와요.',
    inputs: [
      {
        name: 'kind', label: '소득 종류', type: 'select', default: 'p60',
        options: [
          ['p60', '강연료·원고료·인세·자문료 등 일시적 용역 (필요경비 60%)'],
          ['p80', '공익법인 시상 상금·여러 명이 순위를 겨루는 대회 상금·주택입주 지체상금 (필요경비 80%)'],
          ['p0', '사례금·물품·장소 일시 대여료·위약금 등 (정해진 필요경비 없음)'],
        ],
        help: '계속·반복해서 하는 일의 대가는 기타소득이 아니라 사업소득(3.3%)이라 이 계산과 달라요. 복권·연금계좌 연금외수령·소기업소상공인 공제부금 해지일시금·서화·골동품·종교인소득은 세율이나 비과세 기준이 달라 이 계산기로 계산하지 않아요.',
      },
      { name: 'amount', label: '받기로 한 금액 (원, 세전)', type: 'number', min: 0, step: 1, help: '1건(한 번 지급) 기준이에요.' },
      { name: 'actual', label: '실제로 쓴 필요경비 (원, 없으면 비워 둬요)', type: 'number', min: 0, step: 1, help: '증빙이 있고 정해진 비율보다 클 때만 결과가 달라져요.' },
    ],

    compute(v, d, u) {
      const amount = Number(v.amount);
      if (!(amount > 0)) throw new u.InputError('받기로 한 금액을 넣으면 결과가 나와요.');
      if (!Number.isInteger(amount)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (amount > d.max_input) throw new u.InputError('금액이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      const actualRaw = v.actual === undefined || v.actual === null || v.actual === '' ? 0 : Number(v.actual);
      if (!(actualRaw >= 0) || !Number.isInteger(actualRaw)) throw new u.InputError('실제 필요경비는 0 이상 1원 단위 정수로 넣어 주세요.');
      if (actualRaw > d.max_input) throw new u.InputError('실제 필요경비가 너무 커요.');
      const pct = d.expense_pct[v.kind];
      if (pct === undefined) throw new u.InputError('소득 종류를 골라 주세요.');
      const f = u.floor;
      // 1/100원 단위 정수로 계산해 소수 오차를 없앰
      const deemed100 = amount * pct;
      const actual100 = actualRaw * 100;
      const usedActual = actual100 > deemed100;
      const expense100 = Math.min(Math.max(deemed100, actual100), amount * 100);
      const income100 = amount * 100 - expense100;
      const exempt = income100 <= d.exempt_max * 100;
      let incomeTax = 0;
      let localTax = 0;
      if (!exempt) {
        // 소득세 = 기타소득금액 × 20%, 10원 미만 버림 (국고금관리법 제47조①)
        incomeTax = f((income100 * d.rate_pct) / 100 / 100 / 10) * 10;
        localTax = f((incomeTax * d.local_pct) / 100 / 10) * 10;
      }
      const total = incomeTax + localTax;
      const net = amount - total;
      const expense = expense100 / 100;
      const income = income100 / 100;
      const values = { expense, income, income_tax: incomeTax, local_tax: localTax, total, net, exempt };
      const fmt = (n) => (Number.isInteger(n) ? u.won(n) : `${n.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}원`);
      const rows = [['받기로 한 금액 (세전)', u.won(amount)]];
      rows.push([usedActual ? (actual100 > amount * 100 ? '필요경비 (실제 쓴 금액, 받은 금액까지만)' : '필요경비 (실제 쓴 금액)') : (pct === 0 ? '필요경비 (정해진 비율 없음)' : `필요경비 (받은 금액의 ${pct}%)`), fmt(expense)]);
      rows.push(['기타소득금액 (금액 - 필요경비)', fmt(income)]);
      const notes = [];
      if (exempt) {
        rows.push(['세금', `0원 (기타소득금액 ${u.won(d.exempt_max)} 이하는 과세하지 않음)`]);
        notes.push('기타소득금액이 1건에 5만 원 이하면 소득세를 매기지 않아요 (소득세법 제84조).');
      } else {
        rows.push([`소득세 (기타소득금액의 ${d.rate_pct}%)`, u.won(incomeTax)]);
        rows.push([`지방소득세 (소득세의 ${d.local_pct}%)`, u.won(localTax)]);
        rows.push(['세금 합계', u.won(total)]);
      }
      rows.push(['실수령액', u.won(net)]);
      rows.push(['받은 금액 대비 세금', `${(Math.round((total / amount) * 10000) / 100).toFixed(2)}%`]);
      if (pct === 0 && !usedActual) notes.push('사례금처럼 정해진 필요경비 비율이 없는 소득은 실제로 쓴 경비만 빼요.');
      if (usedActual) notes.push('실제 쓴 필요경비가 정해진 비율보다 커서 실제 금액을 뺐어요. 증빙을 보관해 두세요.');
      notes.push('세금은 10원 미만을 버렸어요.');
      notes.push(`한 해 기타소득금액 합계가 ${u.won(d.separate_max)} 이하면 원천징수로 끝내거나 종합소득세 신고 때 합산하는 것 중 고를 수 있고, 넘으면 다음 해 5월에 합산해 신고해요.`);
      return { values, rows, notes };
    },
  });
})();
