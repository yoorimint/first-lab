/* 기부금 세액공제 계산기 (연말정산, 2026년에 낸 기부금 기준)
   - 정치자금: 10만 원까지 110분의 100, 넘는 금액 15% (조세특례제한법 제76조①, 3천만 원 넘는 25% 구간은 받지 않음 — 입력 상한)
   - 고향사랑: 10만 원까지 110분의 100, 10만 초과 20만 이하 40%, 20만 초과 15% (조세특례제한법 제58조①, 2026년 기부분부터)
   - 특례·일반: 한도 안에서 합계 1천만 원까지 15%, 넘는 금액 30% (소득세법 제59조의4④)
     한도는 시행령 제118조의7①이 준용하는 제81조④: 소득금액에서 정치자금 → 고향사랑 → 특례기부금 순서로 빼고(1호, 특례 한도 = 남은 금액),
     일반 = 그 뒤 남은 금액의 30%, 종교단체가 있으면 10% + min(20%, 종교 외 기부액) (3호)
   요율·금액은 data/donation-tax-credit.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'donation-tax-credit',
    title: '기부금 세액공제 계산기',
    intro: '근로소득금액과 한 해 동안 낸 기부금을 종류별로 넣으면 한도를 적용한 기부금 세액공제액과 다음 해로 넘길 금액이 나와요.',
    inputs: [
      { name: 'income', label: '근로소득금액 (원, 총급여에서 근로소득공제를 뺀 금액)', type: 'number', min: 0, step: 1, help: '원천징수영수증의 근로소득금액이에요. 특례·일반기부금 한도를 이 금액으로 계산해요.' },
      { name: 'political', label: '정치자금 기부금 (원, 본인이 낸 것만)', type: 'number', min: 0, step: 1 },
      { name: 'hometown', label: '고향사랑 기부금 (원, 본인이 낸 것만, 연 2,000만 원까지)', type: 'number', min: 0, step: 1 },
      { name: 'special', label: '특례기부금 (원, 국가·지자체·이재민 구호금 등)', type: 'number', min: 0, step: 1, help: '본인과 기본공제대상자(나이 무관, 소득요건 충족)가 낸 금액을 더해요.' },
      { name: 'general', label: '일반기부금 중 종교단체 외 (원, 사회복지법인 등)', type: 'number', min: 0, step: 1 },
      { name: 'religious', label: '일반기부금 중 종교단체 (원)', type: 'number', min: 0, step: 1 },
    ],

    compute(v, d, u) {
      const num = (x) => (x === undefined || x === null || x === '' ? 0 : Number(x));
      const check = (x, label) => {
        if (!(x >= 0) || !Number.isInteger(x)) throw new u.InputError(`${label} 칸에는 0 이상 1원 단위 정수를 넣어 주세요.`);
        if (x > d.max_input) throw new u.InputError(`${label} 칸의 금액이 너무 커요.`);
      };
      if (v.income === undefined || v.income === null || v.income === '') throw new u.InputError('근로소득금액을 넣어 주세요.');
      const income = num(v.income);
      const political = num(v.political);
      const hometown = num(v.hometown);
      const special = num(v.special);
      const general = num(v.general);
      const religious = num(v.religious);
      check(income, '근로소득금액');
      check(political, '정치자금 기부금');
      check(hometown, '고향사랑 기부금');
      check(special, '특례기부금');
      check(general, '종교단체 외 일반기부금');
      check(religious, '종교단체 기부금');
      if (political + hometown + special + general + religious === 0) throw new u.InputError('기부금을 하나 이상 넣어 주세요.');
      if (political > d.political_cap) throw new u.InputError('정치자금 기부금은 3,000만 원까지만 계산해요 (넘는 금액은 25% 구간이라 따로 확인해요).');
      if (hometown > d.hometown_cap) throw new u.InputError('고향사랑 기부금은 한 사람이 1년에 2,000만 원까지만 낼 수 있어요.');

      // 정치자금 (조특법 제76조①)
      const pFull = Math.min(political, d.full_upto);
      const politicalCredit = u.floor((pFull * 100) / 110 + ((political - pFull) * d.political_rate_pct) / 100); // 한 계산식이라 끝에 한 번 버림

      // 고향사랑 (조특법 제58조①)
      const hFull = Math.min(hometown, d.full_upto);
      const hMid = Math.min(Math.max(hometown - d.full_upto, 0), d.hometown_mid_upto - d.full_upto);
      const hRest = Math.max(hometown - d.hometown_mid_upto, 0);
      const hometownCredit = u.floor((hFull * 100) / 110 + (hMid * d.hometown_mid_rate_pct) / 100 + (hRest * d.hometown_rate_pct) / 100);

      // 특례·일반 (소득세법 제59조의4④)
      // 시행령 제81조④1호·3호 (제118조의7① 준용): 정치자금 → 고향사랑 → 특례 순서로 소득금액에서 뺌
      let rest = income;
      rest -= Math.min(political, rest);
      rest -= Math.min(hometown, rest);
      const specialLimit = rest;
      const specialUsed = Math.min(special, specialLimit);
      const base = rest - specialUsed;
      const limit = religious > 0
        ? u.floor((base * d.religious_limit_pct) / 100 + Math.min((base * d.religious_mix_pct) / 100, general))
        : u.floor((base * d.general_limit_pct) / 100);
      const generalUsed = Math.min(general, limit);
      const religiousUsed = Math.min(religious, limit - generalUsed);
      const eligible = specialUsed + generalUsed + religiousUsed;
      const low = Math.min(eligible, d.high_from);
      const credit = u.floor((low * d.rate_low_pct) / 100 + ((eligible - low) * d.rate_high_pct) / 100);
      const carry = special + general + religious - eligible;
      const total = politicalCredit + hometownCredit + credit;

      const rows = [];
      const notes = [];
      if (political > 0) rows.push(['정치자금 세액공제', u.won(politicalCredit)]);
      if (hometown > 0) rows.push(['고향사랑 세액공제', u.won(hometownCredit)]);
      if (special + general + religious > 0) {
        if (special > 0) rows.push(['특례기부금 인정액 (정치자금·고향사랑과 합쳐 근로소득금액까지)', u.won(specialUsed)]);
        if (general + religious > 0) rows.push(['일반기부금 한도 (근로소득금액에서 정치자금·고향사랑·특례 인정액을 뺀 금액 기준)', u.won(limit)]);
        if (general > 0) rows.push(['종교단체 외 일반기부금 인정액', u.won(generalUsed)]);
        if (religious > 0) rows.push(['종교단체 기부금 인정액', u.won(religiousUsed)]);
        rows.push([`특례·일반 세액공제 (1,000만 원까지 ${d.rate_low_pct}%, 넘는 금액 ${d.rate_high_pct}%)`, u.won(credit)]);
      }
      rows.push(['기부금 세액공제 합계', u.won(total)]);
      if (carry > 0) {
        rows.push(['한도를 넘어 다음 해로 넘길 금액', u.won(carry)]);
        notes.push(`한도를 넘은 ${u.won(carry)}은 다음 해부터 10년 안에 이월해서 공제받을 수 있어요.`);
      }
      if (political + hometown > 0 && special + general + religious > 0) notes.push('정치자금·고향사랑 기부금은 일반기부금 한도를 계산하기 전에 근로소득금액에서 먼저 빼요.');
      if (political + hometown > 0 && special > 0 && political + hometown + special > income) notes.push('정치자금·고향사랑·특례기부금 합계가 근로소득금액을 넘으면, 이 계산은 정치자금·고향사랑을 먼저 빼고 특례기부금 인정액을 구했어요. 넘는 특례기부금 이월액은 회사·세무서에 확인해요.');
      if (hometown > 0) notes.push('고향사랑 기부금은 2026년에 낸 것부터 10만 원 넘고 20만 원까지인 부분이 40%예요. 특별재난지역에 낸 20만 원 넘는 부분은 30%라서 이 계산보다 많아요.');
      if (political + hometown > 0) notes.push('정치자금·고향사랑 기부금은 한도를 넘거나 공제를 다 못 받아도 다음 해로 넘어가지 않아요.');
      if (special + general + religious > 0) notes.push('특례·일반기부금 공제는 특별세액공제라서, 받으면 근로자 표준세액공제 13만 원은 받지 못해요.');
      notes.push('낼 세금(산출세액)보다 많이 공제받지는 못해요. 원 미만은 버렸어요.');
      return {
        values: { politicalCredit, hometownCredit, specialLimit, specialUsed, limit, generalUsed, religiousUsed, eligible, credit, carry, total },
        rows,
        notes,
      };
    },
  });
})();
