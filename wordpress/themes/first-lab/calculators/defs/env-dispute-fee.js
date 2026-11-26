/* 환경분쟁조정 수수료 계산기: 중앙환경분쟁조정피해구제위원회 신청 수수료 (환경분쟁조정피해구제법 시행령 제28조·별표 1).
 * 알선 정액, 조정·책임재정·중재는 조정가액 500만 원 초과분 1만 원마다 더함, 원인재정은 신청인 수 × 정액. 요율은 data/env-dispute-fee.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'env-dispute-fee',
    title: '환경분쟁조정 수수료 계산기',
    intro: '신청 종류와 조정가액을 넣으면 환경분쟁조정 신청 수수료가 나와요. 중앙위원회 수수료표 기준이에요.',
    inputs: [
      { name: 'kind', label: '신청 종류', type: 'choice', default: 'mediation', options: [['conciliation', '알선'], ['mediation', '조정'], ['cause', '원인재정'], ['liability', '책임재정·중재']] },
      { name: 'amount_man', label: '조정가액 (만 원)', type: 'number', min: 0, step: 1, default: 1000, help: '조정으로 받으려는 금액을 만 원 단위로 넣어요 (1,000만 원이면 1000). 금액을 정할 수 없으면 500을 넣어요.' },
      { name: 'people', label: '신청인 수 (원인재정만)', type: 'number', min: 1, step: 1, default: 1 },
    ],

    compute(v, d, u) {
      const kind = v.kind;
      const rows = [];
      const notes = [];
      let fee = 0;
      if (kind === 'conciliation') {
        fee = d.conciliation_fee;
        rows.push(['신청 종류', '알선 (조정가액과 관계없이 정액)']);
      } else if (kind === 'cause') {
        const people = Number(v.people);
        if (!(people >= 1) || !Number.isInteger(people)) throw new u.InputError('신청인 수를 1명 이상 정수로 넣어 주세요.');
        if (people > d.max_people) throw new u.InputError('신청인 수가 너무 많아요. 10만 명 이하로 넣어 주세요.');
        fee = people * d.cause_fee_per_person;
        rows.push(['신청 종류', `원인재정 (신청인 ${people.toLocaleString('ko-KR')}명 × ${u.won(d.cause_fee_per_person)})`]);
      } else {
        const man = Number(v.amount_man);
        if (!(man > 0)) throw new u.InputError('조정가액을 만 원 단위로 넣으면 결과가 나와요.');
        if (!Number.isInteger(man)) throw new u.InputError('조정가액은 만 원 단위 정수로 넣어 주세요.');
        if (man > d.max_amount_man) throw new u.InputError('조정가액이 너무 커요. 1조 원 이하로 넣어 주세요.');
        const t = kind === 'liability' ? d.liability : d.mediation;
        const mid = Math.max(0, Math.min(man, d.tier2_upto_man) - d.base_upto_man); // 500만 원 초과 5,000만 원까지 (1만 원 단위 수)
        const high = Math.max(0, man - d.tier2_upto_man); // 5,000만 원 초과 (1만 원 단위 수)
        fee = t.base + mid * t.per_10k_mid + high * t.per_10k_high;
        rows.push(['신청 종류', kind === 'liability' ? '책임재정·중재' : '조정']);
        rows.push(['조정가액', u.won(man * 10000)]);
        rows.push(['500만 원까지', u.won(t.base)]);
        if (mid > 0) rows.push([`500만 원 넘는 부분 (1만 원마다 ${t.per_10k_mid}원)`, u.won(mid * t.per_10k_mid)]);
        if (high > 0) rows.push([`5,000만 원 넘는 부분 (1만 원마다 ${t.per_10k_high}원)`, u.won(high * t.per_10k_high)]);
        if (kind === 'liability' && man > d.local_limit_man) notes.push('조정가액이 1억 원을 넘는 책임재정·중재는 중앙위원회에 신청해요.');
      }
      rows.push(['신청 수수료', u.won(fee)]);
      notes.push('중앙위원회 수수료는 수입인지로 내고, 위원회가 정하면 전자결제로도 낼 수 있어요.');
      notes.push('지방위원회 수수료는 시·도 조례로 정해요. 서울시 위원회도 같은 금액을 안내해요.');
      if (kind === 'cause') notes.push('원인재정은 중앙위원회만 맡아요.');
      return { values: { fee }, rows, notes };
    },
  });
})();
