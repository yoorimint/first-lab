/* 의료비 세액공제 계산기: 총급여의 3%(문턱)를 넘게 쓴 의료비에 15%(미숙아·선천성이상아 20%, 난임시술비 30%).
   문턱은 일반 가족 의료비에서 먼저 빼고, 모자라면 본인·6세 이하·65세 이상·장애인 등 → 미숙아·선천성이상아 → 난임시술비 순서로 뺀다.
   일반 가족 의료비만 연 700만 원 한도. 공제율·한도는 data/medical-tax-credit.json (소득세법 제59조의4②, 시행령 제118조의5) */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'medical-tax-credit',
    title: '의료비 세액공제 계산기',
    intro: '총급여와 1년 동안 낸 의료비를 사람별로 나눠 넣으면, 소득세에서 빼 주는 의료비 세액공제액이 나와요.',
    inputs: [
      { name: 'salary', label: '총급여 (원)', type: 'number', min: 0, step: 10000, help: '1년 급여에서 비과세 소득(식대 등)을 뺀 금액이에요. 근로소득 원천징수영수증의 총급여 칸 값이에요.' },
      { name: 'special', label: '본인·65세 이상 등 의료비 (원)', type: 'number', min: 0, step: 1, help: '한도 없는 의료비예요: 나, 그해 1월 1일 기준 6세 이하, 12월 31일 기준 65세 이상, 장애인, 중증·희귀난치성질환자·결핵환자(산정특례 등록)를 위해 낸 돈이에요. 없으면 비워 둬요.' },
      { name: 'general', label: '그 밖의 가족 의료비 (원)', type: 'number', min: 0, step: 1, help: '배우자·자녀·부모님 등 위 칸에 안 들어가는 가족의 의료비예요. 나이·소득은 안 봐요. 없으면 비워 둬요.' },
      { name: 'premature', label: '미숙아·선천성이상아 의료비 (원)', type: 'number', min: 0, step: 1, help: '미숙아·선천성이상아 치료에 낸 의료비예요. 공제율 20%예요.' },
      { name: 'ivf', label: '난임시술비 (원)', type: 'number', min: 0, step: 1, help: '보조생식술 비용과 관련 처방 의약품 비용이에요. 공제율 30%예요.' },
    ],

    compute(v, d, u) {
      const num = (x) => (x === undefined || x === null || x === '' ? 0 : Number(x));
      const check = (x, label) => {
        if (!(x >= 0) || !Number.isInteger(x)) throw new u.InputError(`${label} 칸에는 0 이상 1원 단위 정수를 넣어 주세요.`);
        if (x > d.max_input) throw new u.InputError(`${label} 칸의 금액이 너무 커요.`);
      };
      const salary = num(v.salary);
      if (!(salary > 0)) throw new u.InputError('총급여를 넣으면 결과가 나와요.');
      check(salary, '총급여');
      const general = num(v.general);
      const special = num(v.special);
      const premature = num(v.premature);
      const ivf = num(v.ivf);
      check(general, '그 밖의 가족 의료비');
      check(special, '본인·6세 이하·65세 이상·장애인 등 의료비');
      check(premature, '미숙아·선천성이상아 의료비');
      check(ivf, '난임시술비');
      if (general + special + premature + ivf === 0) throw new u.InputError('의료비를 하나 이상 넣어 주세요.');

      const threshold = (salary * d.threshold_pct) / 100; // 법에 끝수 규정이 없어 버리지 않음 (원 미만은 마지막 공제액에서만 버림)
      // 문턱은 일반 → 본인 등 → 미숙아 → 난임 순서로 뺀다 (제59조의4②1호~4호 단서)
      const a1 = Math.min(Math.max(general - threshold, 0), d.cap_general);
      let short = Math.max(threshold - general, 0);
      const a2 = Math.max(special - short, 0);
      short = Math.max(short - special, 0);
      const a3 = Math.max(premature - short, 0);
      short = Math.max(short - premature, 0);
      const a4 = Math.max(ivf - short, 0);
      short = Math.max(short - ivf, 0);

      const credit = u.floor((d.rate_pct * (a1 + a2) + d.rate_premature_pct * a3 + d.rate_ivf_pct * a4) / 100);
      const eligible = a1 + a2 + a3 + a4;
      const values = { threshold, a1, a2, a3, a4, eligible, credit, unused: short };
      const rows = [
        [`문턱 (총급여의 ${d.threshold_pct}%)`, u.won(threshold)],
        ['그 밖의 가족 의료비 인정액', `${u.won(a1)}${general - threshold > d.cap_general ? ` (한도 ${u.won(d.cap_general)})` : ''}`],
        ['본인·6세 이하·65세 이상·장애인 등 인정액', u.won(a2)],
      ];
      if (premature > 0) rows.push(['미숙아·선천성이상아 인정액', u.won(a3)]);
      if (ivf > 0) rows.push(['난임시술비 인정액', u.won(a4)]);
      rows.push([`세액공제액 (${d.rate_pct}%·${d.rate_premature_pct}%·${d.rate_ivf_pct}%)`, u.won(credit)]);

      const notes = [];
      if (short > 0) notes.push(`의료비 합계가 문턱보다 ${u.won(short)} 적어서 공제받을 금액이 없어요.`);
      if (general - threshold > d.cap_general) notes.push(`그 밖의 가족 의료비는 문턱을 뺀 뒤 연 ${u.won(d.cap_general)}까지만 인정돼요.`);
      notes.push('실손보험금으로 돌려받은 금액, 건강보험 본인부담상한제 환급금은 빼고 넣어요.');
      notes.push('안경·콘택트렌즈는 1명당 연 50만 원, 산후조리원은 출산 1회당 200만 원까지만 넣어요.');
      notes.push('낼 세금(산출세액)보다 많이 공제받지는 못해요. 소득세가 줄면 지방소득세(소득세의 10%)도 같이 줄어요. 원 미만은 버렸어요.');
      return { values, rows, notes };
    },
  });
})();
