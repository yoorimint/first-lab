/* 비상장주식 양도소득세 계산기: 판 금액 - 산 금액 - 필요경비(증권거래세 0.35% 포함) = 양도소득금액,
   기본공제 연 250만 원(국내·해외 과세대상 주식 합쳐 한 번) 뺀 과세표준 × 세율 + 지방소득세(세율의 10분의 1).
   세율: 대주주 아님 중소기업 10%·그 밖 20%, 대주주 3억 이하 20%·초과 25%, 대주주 중소기업 외 1년 미만 30%.
   10원 미만 버림은 국고금관리법 제47조·지방회계법 제55조. 값은 data/unlisted-stock-tax.json
   (소득세법 제94조①3호나목·제103조·제104조①11호, 지방세법 제103조의3, 증권거래세법 제8조①) */
(function () {
  'use strict';

  const num = (raw, name, u, d) => {
    const n = raw === undefined || raw === null || raw === '' ? 0 : Number(raw);
    if (!(n >= 0) || !Number.isInteger(n)) throw new u.InputError(`${name}은 0 이상 1원 단위 정수로 넣어 주세요.`);
    if (n > d.max_input) throw new u.InputError(`${name}이 너무 커요. 1,000억 원 이하로 넣어 주세요.`);
    return n;
  };

  FirstLabCalc.register({
    id: 'unlisted-stock-tax',
    title: '비상장주식 양도소득세 계산기',
    intro: '비상장주식을 판 금액·산 금액과 주주 구분을 고르면 증권거래세 0.35%, 기본공제 250만 원을 반영한 양도소득세·지방소득세가 나와요.',
    inputs: [
      { name: 'sale', label: '판 금액 (양도가액)', type: 'number', min: 0, step: 1, help: '실제로 받은 금액이에요. 가족 등 특수관계인에게 시가보다 싸게 팔면 시가로 다시 계산될 수 있어요.' },
      { name: 'acquire', label: '그 주식을 산 금액 (취득가액)', type: 'number', min: 0, step: 1 },
      { name: 'expense', label: '증권거래세 말고 팔 때 든 비용 (원, 없으면 비워 둬요)', type: 'number', min: 0, step: 1, help: '중개 수수료, 계약서 작성비 같은 비용이에요. 증권거래세는 자동으로 빼요.' },
      { name: 'used', label: '같은 해 다른 주식에서 이미 쓴 기본공제 (원, 없으면 비워 둬요)', type: 'number', min: 0, step: 1 },
      { name: 'kind', label: '주주 구분·주식 종류', type: 'select', default: 'sme', options: [
        ['sme', '대주주 아님 · 중소기업 주식 (10%)'],
        ['general', '대주주 아님 · 중소기업 외 주식 (20%)'],
        ['major', '대주주 (3억 이하 20%, 초과 25%)'],
        ['major_short', '대주주 · 중소기업 외 · 1년 미만 보유 (30%)'],
      ] },
    ],

    compute(v, d, u) {
      const sale = Number(v.sale);
      if (!(sale > 0)) throw new u.InputError('판 금액을 넣으면 결과가 나와요.');
      if (!Number.isInteger(sale)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (sale > d.max_input) throw new u.InputError('금액이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      const acquire = num(v.acquire, '산 금액', u, d);
      const expense = num(v.expense, '비용', u, d);
      const used = num(v.used, '이미 쓴 기본공제', u, d);
      const kind = ['sme', 'general', 'major', 'major_short'].includes(v.kind) ? v.kind : 'sme';
      const f = u.floor;
      // 증권거래세: 양도가액 × 1만분의 35, 10원 미만 버림
      const stt = f((sale * d.stt_per10000) / 10000 / 10) * 10;
      const income = sale - acquire - expense - stt;
      const deduction = Math.max(0, Math.min(d.basic_deduction - Math.min(used, d.basic_deduction), Math.max(0, income)));
      const base = Math.max(0, income - deduction);
      let raw;
      let label;
      if (kind === 'major') {
        raw = base <= d.major_step ? (base * d.major_low_pct) / 100 : d.major_step_tax + ((base - d.major_step) * d.major_high_pct) / 100;
        label = base <= d.major_step ? `${d.major_low_pct}%` : `6,000만 원 + 3억 초과분 ${d.major_high_pct}%`;
      } else {
        const pct = kind === 'sme' ? d.sme_pct : kind === 'general' ? d.general_pct : d.major_short_pct;
        raw = (base * pct) / 100;
        label = `${pct}%`;
      }
      // 지방소득세는 같은 구간의 1천분의 세율(소득세율의 10분의 1)
      const incomeTax = f(raw / 10) * 10;
      const localTax = f(raw / 10 / 10) * 10;
      const total = incomeTax + localTax;
      const all = total + stt;
      const values = { stt, income, deduction, base, income_tax: incomeTax, local_tax: localTax, total, all };
      const rows = [
        ['판 금액', u.won(sale)],
        ['증권거래세 (판 금액의 0.35%)', u.won(stt)],
        ['산 금액 + 그 밖의 비용', u.won(acquire + expense)],
        [income < 0 ? '양도소득금액 (손실)' : '양도소득금액', u.won(income)],
        ['기본공제', u.won(deduction)],
        ['과세표준', u.won(base)],
        [`양도소득세 (${label})`, u.won(incomeTax)],
        ['지방소득세 (양도소득세의 10%)', u.won(localTax)],
        ['양도소득세 + 지방소득세', u.won(total)],
        ['증권거래세까지 합친 세금', u.won(all)],
      ];
      const notes = [];
      if (income <= 0) notes.push('손실이라 양도소득세는 0원이지만, 증권거래세는 손실이어도 판 금액에 붙어요.');
      else if (base === 0) notes.push('양도소득금액이 기본공제 250만 원 안이라 양도소득세가 0원이에요.');
      notes.push('세금은 10원 미만을 버렸어요.');
      notes.push('K-OTC에서 판 중소·중견기업 주식(대주주 아님)은 양도소득세가 없고 증권거래세율도 달라 이 계산과 맞지 않아요.');
      return { values, rows, notes };
    },
  });
})();
