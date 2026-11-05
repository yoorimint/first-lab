/* 부동산 양도소득세 계산기: 양도가액·취득가액·필요경비·보유/거주기간·주택 상황 → 양도차익, 장기보유특별공제, 과세표준, 세율, 양도소득세, 지방소득세.
   12억 원 고가주택 안분(소득세법 시행령 제160조), 장특공 표1·표2(소득세법 제95조), 단기·중과 세율(제104조). 요율은 data/capital-gains-tax.json */
(function () {
  'use strict';

  /** 과세표준 × (기본세율 + 가산) − 누진공제 (원 미만 버림). 세율은 % 정수로 계산 */
  function progressive(base, brackets, addPct) {
    const b = brackets.find((x) => x.upto === null || base <= x.upto);
    return { tax: Math.max(0, Math.floor((base * (b.rate_pct + addPct)) / 100) - b.deduction), rate: b.rate_pct + addPct };
  }
  const flat = (base, pct) => ({ tax: Math.floor((base * pct) / 100), rate: pct });
  const bigger = (a, b) => (b.tax > a.tax ? b : a);

  FirstLabCalc.register({
    id: 'capital-gains-tax',
    title: '부동산 양도소득세 계산기',
    intro: '판 가격, 산 가격, 필요경비, 보유·거주 기간을 넣으면 장기보유특별공제, 과세표준, 양도소득세와 지방소득세를 계산해요.',
    inputs: [
      { name: 'asset', label: '파는 부동산', type: 'select', default: 'house', options: [['house', '주택 (아파트·빌라·단독주택)'], ['land', '토지·상가 등 주택 외 건물']] },
      { name: 'status', label: '주택 상황', type: 'select', default: 'one', help: '토지·상가는 "일반"을 골라요.',
        options: [['one', '1세대 1주택 비과세 요건 충족 (2년 보유 등)'], ['general', '일반 (비과세 아님, 중과 아님)'], ['heavy2', '조정대상지역 2주택 중과'], ['heavy3', '조정대상지역 3주택 이상 중과']] },
      { name: 'sale', label: '양도가액 (판 가격, 원)', type: 'number', min: 0, step: 1000000 },
      { name: 'acquire', label: '취득가액 (산 가격, 원)', type: 'number', min: 0, step: 1000000, help: '취득세 같은 취득 부대비용은 여기에 더해 넣어요.' },
      { name: 'expense', label: '필요경비 (원)', type: 'number', min: 0, step: 100000, default: 0, help: '취득가액 말고 따로 든 자본적지출액과 양도비(중개수수료 등)예요.' },
      { name: 'hold', label: '보유기간 (만 몇 년)', type: 'number', min: 0, step: 1, default: 0, help: '취득일부터 양도일까지 꽉 채운 햇수 (1년 미만이면 0)' },
      { name: 'live', label: '거주기간 (만 몇 년)', type: 'number', min: 0, step: 1, default: 0, help: '1세대 1주택만 써요. 그 집에 실제로 산 기간이에요.' },
    ],

    compute(v, d, u) {
      const asset = v.asset === 'land' ? 'land' : 'house';
      const status = asset === 'land' ? 'general' : (v.status || 'one');
      const sale = Number(v.sale);
      const acquire = Number(v.acquire);
      const expense = Number(v.expense) || 0;
      const hold = Number(v.hold ?? 0);
      const live = Number(v.live ?? 0);
      if (!(sale > 0) || !(acquire >= 0) || Number.isNaN(acquire)) throw new u.InputError('양도가액과 취득가액을 넣으면 결과가 나와요.');
      if (expense < 0) throw new u.InputError('필요경비는 0 이상으로 넣어 주세요.');
      if (!Number.isInteger(hold) || hold < 0 || !Number.isInteger(live) || live < 0) throw new u.InputError('보유·거주 기간은 0 이상의 정수(만 몇 년)로 넣어 주세요.');
      if (live > hold) throw new u.InputError('거주기간은 보유기간보다 길 수 없어요.');
      if (status === 'one' && hold < 2) throw new u.InputError('1세대 1주택 비과세는 2년 이상 보유해야 해요. 2년 미만이면 "일반"으로 계산해요.');

      const gain = Math.max(0, sale - acquire - expense);
      const notes = [];
      if (status === 'one' && sale <= d.high_price) {
        return {
          values: { gain, taxable_gain: 0, lthd_pct: 0, lthd: 0, income: 0, base: 0, rate: 0, tax: 0, local: 0, total: 0, exempt: true },
          rows: [['양도차익', u.won(gain)], ['결과', '양도가액 12억 원 이하 1세대 1주택이라 비과세 (낼 세금 0원)']],
          notes: ['비과세여도 조정대상지역에서 취득한 집은 2년 이상 거주해야 하는 등 요건이 있어요. 요건은 홈택스 모의계산이나 세무서에서 한 번 더 확인해요.'],
        };
      }

      // 12억 초과 1세대 1주택: 12억 넘는 부분 비율만 과세 (시행령 제160조)
      const taxableGain = status === 'one' ? Math.floor((gain * (sale - d.high_price)) / sale) : gain;

      let lthdPct = 0;
      if (status === 'one' && hold >= d.lthd_one_house.min_hold && live >= d.lthd_one_house.min_live) {
        const t = d.lthd_one_house;
        lthdPct = Math.min(t.hold_max_pct, hold * t.hold_pct_per_year) + Math.min(t.live_max_pct, live * t.live_pct_per_year);
      } else if (!status.startsWith('heavy') && hold >= d.lthd_general.min_years) {
        lthdPct = Math.min(d.lthd_general.max_pct, hold * d.lthd_general.pct_per_year);
      }
      const lthd = Math.floor((taxableGain * lthdPct) / 100);
      const income = taxableGain - lthd;
      const base = Math.max(0, income - d.basic_deduction);

      const add = status.startsWith('heavy') ? d.heavy_add_pct[status] : 0;
      const sr = d.short_rates[asset];
      let r = progressive(base, d.brackets, add);
      if (hold < 1) r = bigger(r, flat(base, sr.under1));
      else if (hold < 2) r = bigger(r, flat(base, sr.under2));
      const tax = r.tax;
      const local = Math.floor(tax / d.local_ratio_div);

      if (status === 'one') notes.push('12억 원을 넘는 1세대 1주택이라 양도차익 중 (양도가액 − 12억 원) ÷ 양도가액 비율만 과세했어요.');
      if (status === 'one' && live < d.lthd_one_house.min_live && hold >= 3) notes.push('거주기간이 2년 미만이라 장기보유특별공제는 1년에 2%(최대 30%) 표로 계산했어요.');
      if (status === 'one' && live < d.lthd_one_house.min_live) notes.push('취득할 때 조정대상지역이었던 주택은 2년 이상 거주해야 비과세가 돼요(소득세법 시행령 제154조). 거주 요건을 못 채웠다면 양도차익 전체가 과세될 수 있어요.');
      if (add) notes.push(`중과 대상이라 기본세율에 ${add}%p를 더했고 장기보유특별공제는 빠져요. 2026년 5월 9일까지 계약한 주택 등은 경과 규정으로 중과에서 빠질 수 있으니(소득세법 시행령 제167조의10) 국세청에 확인해요.`);
      notes.push('기본공제 250만 원은 같은 해의 부동산 양도 전체에서 한 번만 받아요(소득세법 제103조). 올해 이미 받았다면 세액이 더 나와요.');
      notes.push('지방소득세는 양도소득세의 10%로 계산했어요. 분양권·조합원입주권·비사업용 토지·미등기, 감면 특례와 2026년 세제개편안(정부안)은 넣지 않았어요.');

      return {
        values: { gain, taxable_gain: taxableGain, lthd_pct: lthdPct, lthd, income, base, rate: r.rate, tax, local, total: tax + local, exempt: false },
        rows: [
          ['양도차익', u.won(gain)],
          ...(status === 'one' ? [['과세되는 양도차익 (12억 초과분)', u.won(taxableGain)]] : []),
          ['장기보유특별공제', `${u.won(lthd)} (${lthdPct}%)`],
          ['양도소득금액', u.won(income)],
          ['과세표준 (기본공제 250만 원 뒤)', u.won(base)],
          ['적용 세율', `${r.rate}%`],
          ['양도소득세', u.won(tax)],
          ['지방소득세', u.won(local)],
          ['합계', u.won(tax + local)],
        ],
        notes,
      };
    },
  });
})();
