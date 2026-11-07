/* 상속세 계산기: 상속재산 − 공과금·채무·장례비 + 사전증여 = 과세가액 → 상속공제(일괄·배우자·금융재산, 종합한도) → 세율 → 증여세액공제·신고세액공제. 값은 data/inheritance-tax.json */
(function () {
  'use strict';

  const HEIRS = [
    ['spouse_children', '배우자와 자녀'],
    ['children', '자녀만 (배우자 없음)'],
    ['spouse_only', '배우자 혼자 (자녀·부모 없음)'],
  ];

  function baseTax(base, brackets) {
    const b = brackets.find((x) => x.upto === null || base <= x.upto);
    return { tax: Math.max(0, FirstLabCalc.util.floor((base * b.rate_pct) / 100) - b.deduction), rate: b.rate_pct };
  }

  const num = (x) => Number(x) || 0;

  FirstLabCalc.register({
    id: 'inheritance-tax',
    title: '상속세 계산기',
    intro: '상속재산과 빚, 상속인 구성을 넣으면 공제 금액, 과세표준, 세율, 신고하면 낼 상속세를 계산해요.',
    inputs: [
      { name: 'estate', label: '상속재산 합계 (원)', type: 'number', min: 0, step: 1000000, help: '사망일 기준 시가로 부동산·예금·주식·보험금 등을 모두 더해요. 미리 증여한 재산은 아래 칸에 따로 넣어요.' },
      { name: 'heirs', label: '상속인 구성', type: 'select', default: 'spouse_children', options: HEIRS },
      { name: 'children', label: '자녀 수 (명)', type: 'number', min: 0, step: 1, default: 2 },
      { name: 'spouse_amount', label: '배우자가 실제로 받는 금액 (원)', type: 'number', min: 0, step: 1000000, default: 0, help: '배우자가 없으면 0. 5억 원보다 적게 받아도 5억 원은 공제돼요.' },
      { name: 'elderly', label: '65세 이상 상속인·동거가족 수 (배우자 제외, 명)', type: 'number', min: 0, step: 1, default: 0 },
      { name: 'financial', label: '그중 순금융재산 (예금·주식 − 금융기관 빚, 원)', type: 'number', min: 0, step: 1000000, default: 0 },
      { name: 'debts', label: '고인의 빚·공과금 (원)', type: 'number', min: 0, step: 1000000, default: 0, help: '대출, 임대보증금, 사망일까지 내야 했던 세금·공공요금처럼 상속인이 실제로 갚는 금액이에요.' },
      { name: 'funeral', label: '장례비 (봉안시설 제외, 원)', type: 'number', min: 0, step: 100000, default: 0, help: '영수증이 없어도 500만 원은 공제되고, 1,000만 원까지 인정돼요.' },
      { name: 'bongan', label: '봉안시설·자연장지 비용 (원)', type: 'number', min: 0, step: 100000, default: 0, help: '500만 원까지 따로 공제돼요.' },
      { name: 'gifts', label: '미리 증여한 재산 (원)', type: 'number', min: 0, step: 1000000, default: 0, help: '사망 전 10년 안에 상속인에게, 5년 안에 상속인이 아닌 사람에게 준 재산 (증여 당시 평가액)' },
      { name: 'gifts_nonheir', label: '그중 상속인이 아닌 사람에게 준 금액 (원)', type: 'number', min: 0, step: 1000000, default: 0, help: '손자녀·사위·며느리처럼 상속인이 아닌 사람에게 준 금액이에요. 배우자 공제 한도 계산에서 빠져요.' },
      { name: 'spouse_gift_base', label: '그중 배우자가 받은 증여의 증여세 과세표준 (원)', type: 'number', min: 0, step: 1000000, default: 0, help: '배우자가 받은 증여재산 − 배우자 증여재산공제(최대 6억 원). 배우자 공제 한도에서 빠져요.' },
      { name: 'gift_deducted', label: '그 증여 때 공제받은 금액 (원)', type: 'number', min: 0, step: 1000000, default: 0, help: '예: 자녀에게 1억 원을 주면서 증여재산공제 5,000만 원을 받았다면 50000000' },
      { name: 'gift_tax_paid', label: '그 증여 때 증여세 산출세액 (원)', type: 'number', min: 0, step: 100000, default: 0, help: '증여세 신고서의 산출세액이에요 (신고세액공제 빼기 전 금액).' },
      { name: 'on_time', label: '기한 안에 신고하나요', type: 'select', default: 'yes', options: [['yes', '네 (신고세액공제 3%)'], ['late', '기한 뒤에 신고'], ['none', '신고하지 않음']] },
    ],

    compute(v, d, u) {
      const estate = num(v.estate);
      const children = num(v.children);
      const elderly = num(v.elderly);
      const fin = num(v.financial);
      const gifts = num(v.gifts);
      const giftDeducted = num(v.gift_deducted);
      const heirs = v.heirs;
      if (!(estate > 0)) throw new u.InputError('상속재산 합계를 넣으면 결과가 나와요.');
      if (!HEIRS.some((h) => h[0] === heirs)) throw new u.InputError('상속인 구성을 골라 주세요.');
      if ([children, elderly, fin, gifts, giftDeducted, num(v.debts), num(v.funeral), num(v.bongan), num(v.spouse_amount), num(v.gift_tax_paid)].some((x) => x < 0)) {
        throw new u.InputError('금액과 사람 수는 0 이상으로 넣어 주세요.');
      }
      if (!Number.isInteger(children) || !Number.isInteger(elderly)) throw new u.InputError('사람 수는 정수로 넣어 주세요.');
      if (heirs === 'spouse_only' && children > 0) throw new u.InputError('자녀가 있으면 상속인 구성을 \'배우자와 자녀\'로 골라 주세요.');
      if (heirs !== 'spouse_only' && children < 1) throw new u.InputError('자녀 수를 1명 이상 넣어 주세요.');
      if (fin > estate) throw new u.InputError('순금융재산은 상속재산 합계보다 클 수 없어요.');
      if (giftDeducted > gifts) throw new u.InputError('공제받은 금액은 미리 증여한 재산보다 클 수 없어요.');
      const giftsNonheir = num(v.gifts_nonheir);
      const spouseGiftBase = num(v.spouse_gift_base);
      if (giftsNonheir < 0 || spouseGiftBase < 0) throw new u.InputError('금액과 사람 수는 0 이상으로 넣어 주세요.');
      if (giftsNonheir > gifts) throw new u.InputError('상속인이 아닌 사람에게 준 금액은 미리 증여한 재산보다 클 수 없어요.');
      if (spouseGiftBase > gifts - giftDeducted) throw new u.InputError('배우자 증여의 과세표준이 미리 증여한 재산에서 공제받은 금액을 뺀 것보다 클 수 없어요.');
      const filed = v.on_time === 'yes' || v.on_time === 'late' || v.on_time === 'no'; // 'no' = 예전 선택지(기한 뒤 신고)

      const f = d.funeral;
      const funeral = Math.min(Math.max(num(v.funeral), f.min), f.max) + Math.min(num(v.bongan), f.bongan_max);
      const items = Math.min(num(v.debts) + funeral, estate);
      const taxable = estate - items + gifts;

      let personal = d.basic + d.per_child * children + d.per_elderly * elderly;
      const lumpOk = heirs !== 'spouse_only';
      if (lumpOk) personal = filed ? Math.max(personal, d.lump_sum) : d.lump_sum; // 무신고면 일괄공제 5억 원만 (제21조① 단서)

      let spouse = 0;
      if (heirs !== 'children') {
        const s = d.spouse;
        // 한도 = (A − B + C) × 법정상속분 − E (제19조①): A − B = 자산총액 − 공과금·채무 (시행령 제17조①, 장례비는 빼지 않음),
        // C = 상속인에게 한 사전증여(상속인 아닌 사람 몫 제외), E = 배우자 사전증여의 증여세 과세표준.
        // 법정상속분은 정수로 계산 (배우자 1.5 : 자녀 1 → 3 : 2) — 소수 곱셈 1원 오차 방지. 11/4·11/5 외부 검수로 정정
        const shareNum = heirs === 'spouse_only' ? 1 : s.share_spouse * 2;
        const shareDen = heirs === 'spouse_only' ? 1 : (s.share_spouse + s.share_child * children) * 2;
        const base0 = estate - Math.min(num(v.debts), estate) + gifts - giftsNonheir;
        const legal = FirstLabCalc.util.floor((base0 * shareNum) / shareDen) - spouseGiftBase;
        spouse = Math.max(s.min, Math.min(num(v.spouse_amount), legal, s.max));
      }

      const fd = d.financial;
      const financial = fin <= fd.full_upto ? fin : Math.min(Math.max(FirstLabCalc.util.floor((fin * fd.rate_pct) / 100), fd.min), fd.max);

      const wanted = personal + spouse + financial;
      const cap = Math.max(0, taxable - (taxable > d.limit_gift_over ? gifts - giftDeducted : 0));
      const total = Math.min(wanted, cap);
      const base = Math.max(0, taxable - total);
      const raw = baseTax(base, d.brackets);
      const rate = raw.rate;
      const tax = base < d.min_taxable_base ? 0 : raw.tax; // 과세표준 50만 원 미만이면 부과하지 않음 (제25조②)
      // 증여세액공제 (제28조): 과세가액 5억 원 이하면 없음, 한도는 산출세액 × (증여재산 과세표준 ÷ 상속세 과세표준)
      const giftBase = gifts - giftDeducted;
      const giftLimit = base > 0 && taxable > d.limit_gift_over ? FirstLabCalc.util.floor((tax * Math.min(giftBase, base)) / base) : 0;
      const giftCredit = Math.min(num(v.gift_tax_paid), giftLimit);
      const credit = v.on_time !== 'yes' ? 0 : FirstLabCalc.util.floor(((tax - giftCredit) * d.filing_credit_pct) / 100);
      // 낼 세액은 10원 미만 버림 (국고금관리법 제47조①, 11/7 #1754 검수: 1원 단위로 보여 줌)
      const pay = FirstLabCalc.util.floor((tax - giftCredit - credit) / 10) * 10;

      const rows = [
        ['장례비 공제', u.won(funeral)],
        ['상속세 과세가액', u.won(taxable)],
        [lumpOk && personal === d.lump_sum ? '일괄공제' : '기초·인적공제', u.won(personal)],
      ];
      if (heirs !== 'children') rows.push(['배우자 상속공제', u.won(spouse)]);
      rows.push(['금융재산 상속공제', u.won(financial)]);
      if (total < wanted) rows.push(['공제 한도 적용 후 공제 합계', u.won(total)]);
      else rows.push(['공제 합계', u.won(total)]);
      rows.push(['과세표준', u.won(base)], ['적용 세율', `${rate}%`], ['산출세액', u.won(tax)]);
      if (giftCredit) rows.push(['증여세액공제', `-${u.won(giftCredit)}`]);
      rows.push(['신고세액공제 (3%)', `-${u.won(credit)}`], ['낼 상속세 (상속인 전체)', u.won(pay)]);

      const notes = [
        '고인이 국내 거주자일 때 기준이에요. 동거주택·가업·영농 상속공제, 미성년자·장애인 공제, 손자녀 할증, 감정평가수수료는 넣지 않았어요.',
        '배우자 공제는 법정상속분 한도로 계산했어요. 배우자가 5억 원 넘게 받으려면 신고기한 다음 날부터 9개월 안에 재산을 나눠 등기 등을 마쳐야 해요.',
      ];
      if (total < wanted) notes.push('미리 증여한 재산이 있어 상속공제가 한도까지만 적용됐어요.');
      if (giftCredit) notes.push('증여세액공제는 상속인마다 자기가 낼 상속세 비율만큼 한도가 따로 있어 실제로는 조금 줄 수 있어요.');
      if (heirs === 'spouse_only') notes.push('배우자 혼자 상속받으면 일괄공제 5억 원 대신 기초공제 2억 원과 인적공제만 받아요.');
      if (v.on_time !== 'yes') notes.push('기한을 넘기면 신고세액공제를 못 받고, 무신고 가산세(낼 세액의 20%, 기한 뒤 빨리 신고하면 일부 감면)가 붙어요.');
      if (!filed && lumpOk) notes.push('신고하지 않으면 자녀 공제가 더 크더라도 일괄공제 5억 원만 받아요.');
      return {
        values: { funeral, taxable, personal, spouse, financial, total, base, rate, tax, gift_credit: giftCredit, credit, pay },
        rows,
        notes,
      };
    },
  });
})();
