/* 재산세 계산기 (주택분, 개인): 공시가격 × 공정시장가액비율 → (과세표준 상한) → 세율(1세대 1주택 9억 이하 특례) → 지방교육세 20% + 도시지역분 0.14%. 끝수: 10원 미만 버림 (지방세기본법 제59조 → 국고금관리법 제47조). 값은 data/property-tax.json */
(function () {
  'use strict';

  const KIND = [
    ['one', '1세대 1주택'],
    ['other', '그 밖의 주택 (2주택 이상인 집 등)'],
  ];
  const URBAN = [
    ['yes', '도시지역분 붙음 (지자체가 고시한 도시지역)'],
    ['no', '도시지역분 없음'],
  ];

  // 누진 구간마다 천분율을 곱해 더한다 (지방세법 제111조①3호나목·제111조의2① 표와 같은 값)
  function progressive(base, brackets) {
    let tax = 0;
    let lo = 0;
    for (const b of brackets) {
      const hi = b.upto === null ? base : Math.min(base, b.upto);
      if (hi > lo) tax += ((hi - lo) * b.permille) / 1000;
      if (b.upto === null || base <= b.upto) break;
      lo = b.upto;
    }
    return FirstLabCalc.util.floor(tax);
  }

  FirstLabCalc.register({
    id: 'property-tax',
    title: '재산세 계산기 (주택분)',
    intro: '주택 공시가격과 1세대 1주택 여부를 넣으면 과세표준, 재산세, 지방교육세, 도시지역분과 합계를 계산해요.',
    inputs: [
      { name: 'price', label: '올해 주택 공시가격 (원)', type: 'number', min: 0, step: 10000000, help: '6월 1일 현재 공시된 그해 그 집의 공시가격이에요. 공동명의여도 집 전체 공시가격을 넣어요.' },
      { name: 'kind', label: '주택 구분', type: 'select', default: 'one', options: KIND },
      { name: 'urban', label: '도시지역분', type: 'select', default: 'yes', options: URBAN },
      { name: 'prev', label: '작년 공시가격 (원, 모르면 0)', type: 'number', min: 0, step: 10000000, default: 0, help: '넣으면 과세표준 상한(올해 과세표준이 너무 오르지 않게 막는 장치)을 함께 계산해요.' },
    ],

    compute(v, d, u) {
      const price = Number(v.price);
      const prev = Number(v.prev) || 0;
      const one = v.kind === 'one';
      if (!(price > 0)) throw new u.InputError('올해 공시가격을 넣으면 결과가 나와요.');
      if (prev < 0) throw new u.InputError('작년 공시가격은 0 이상으로 넣어 주세요.');

      const ratio = one ? d.ratio_one.find((t) => t.upto === null || price <= t.upto).pct : d.ratio_other_pct;
      const base0 = u.floor((price * ratio) / 100);
      let base = base0;
      let capped = false;
      if (prev > 0) {
        const cap = u.floor((prev * ratio) / 100) + u.floor((base0 * d.cap_pct) / 100);
        if (cap < base0) {
          base = cap;
          capped = true;
        }
      }
      const special = one && price <= d.special_max_price;
      const tax = u.floor10(progressive(base, special ? d.brackets_special : d.brackets_standard));
      const edu = u.floor10((tax * d.edu_pct) / 100);
      const urban = v.urban === 'no' ? 0 : u.floor10((base * d.urban_permille) / 1000);
      const total = tax + edu + urban;

      const rows = [
        [`공정시장가액비율`, `${ratio}%`],
        [capped ? '과세표준 (상한 적용)' : '과세표준', u.won(base)],
        [special ? '재산세 (1세대 1주택 특례세율)' : '재산세 (표준세율)', u.won(tax)],
        ['지방교육세 (재산세의 20%)', u.won(edu)],
        [v.urban === 'no' ? '도시지역분 (해당 없음)' : '도시지역분 (과세표준의 0.14%)', u.won(urban)],
        ['합계', u.won(total)],
      ];

      const notes = [
        '시·군·구가 조례로 세율을 50% 범위에서 올리거나 내릴 수 있고, 도시지역분은 0.23%까지 다르게 정할 수 있고(개발제한구역 안 주택은 고급주택만 해당), 감면 대상이면 고지서 금액이 달라요. 세목마다 10원 미만은 버리고 계산했어요.',
        `1세대 1주택 공정시장가액비율 ${d.ratio_one.map((t) => t.pct + '%').join('·')}는 ${d.ratio_one_year}년분 재산세에 정해진 값이에요. 다음 해 비율은 시행령이 바뀌면 달라져요.`,
      ];
      if (special) notes.push(`1세대 1주택 특례세율은 지방세법 부칙(법률 제17769호 제2조)에 따라 ${d.special_last_year}년분 재산세까지 적용돼요. 그다음 해분은 기간을 늘리는 법 개정이 있어야 적용돼요.`);
      if (one && !special) notes.push('공시가격이 9억 원을 넘으면 1세대 1주택이어도 특례세율 대신 표준세율로 계산해요.');
      if (prev === 0) notes.push('작년 공시가격을 넣지 않아 과세표준 상한은 계산하지 않았어요.');
      if (capped) notes.push('올해 과세표준이 상한(작년 공시가격 × 올해 비율 + 올해 과세표준의 5%)보다 커서 상한액으로 계산했어요.');
      notes.push('주택분 재산세는 절반씩 7월 16~31일과 9월 16~30일에 내요. 그해 재산세가 20만 원 이하면 조례에 따라 7월에 한꺼번에 고지될 수 있어요(기준은 시·군·구청에서 확인).');
      if (tax + urban > d.installment_over) notes.push('고지서의 재산세 납부세액이 250만 원을 넘으면 납부기한이 지난 날부터 3개월 안에 일부를 나눠 낼 수 있어요(시·군·구청에서 확인).');
      return {
        values: { ratio_pct: ratio, base, capped: capped ? 1 : 0, special: special ? 1 : 0, tax, edu, urban, total },
        rows,
        notes,
      };
    },
  });
})();
