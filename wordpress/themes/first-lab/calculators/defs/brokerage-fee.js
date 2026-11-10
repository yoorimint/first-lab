/* 중개보수(복비) 계산기: 주택·오피스텔·그 밖의 부동산 매매·전세·월세 중개보수 상한. 요율은 data/brokerage-fee.json (공인중개사법 시행규칙 제20조, 별표 1·2) */
(function () {
  'use strict';

  const pctText = (permil) => `${(permil / 10).toFixed(1)}%`;

  FirstLabCalc.register({
    id: 'brokerage-fee',
    title: '중개보수(복비) 계산기',
    intro: '거래금액을 넣으면 공인중개사에게 줄 중개보수의 상한(최대 금액)을 계산해요. 월세는 보증금과 월세를 같이 넣어요.',
    inputs: [
      {
        name: 'kind', label: '무엇을 거래하나요', type: 'select', default: 'house',
        options: [['house', '주택 (아파트·빌라·단독 등)'], ['officetel', '주거용 오피스텔 (85㎡ 이하·부엌·화장실 등)'], ['other', '그 밖의 부동산 (토지·상가 등)']],
        help: '오피스텔은 전용면적 85㎡ 이하에 전용 입식 부엌·수세식 화장실·목욕시설을 모두 갖춰야 오피스텔 요율이에요. 아니면 "그 밖의 부동산"을 골라요.',
      },
      {
        name: 'deal', label: '거래 종류', type: 'select', default: 'sale',
        options: [['sale', '매매·교환'], ['lease', '임대차 (전세·월세)']],
        help: '교환은 두 부동산 중 비싼 쪽 금액을 넣어요.',
      },
      { name: 'price', label: '매매가 또는 보증금 (원)', type: 'number', min: 0, step: 1000000, help: '매매는 거래가격, 전세·월세는 보증금이에요.' },
      { name: 'rent', label: '월세 (원, 월세일 때만)', type: 'number', min: 0, step: 10000, default: 0 },
    ],

    compute(v, d, u) {
      const price = Number(v.price) || 0;
      const rent = Number(v.rent) || 0;
      if (price < 0 || rent < 0) throw new u.InputError('금액은 0 이상으로 넣어 주세요.');
      const lease = v.deal === 'lease';
      if (!lease && !(price > 0)) throw new u.InputError('매매가를 넣으면 결과가 나와요.');
      if (lease && !(price > 0) && !(rent > 0)) throw new u.InputError('보증금이나 월세를 넣으면 결과가 나와요.');
      if (!lease && rent > 0) throw new u.InputError('월세는 임대차를 골랐을 때만 넣어요.');

      // 거래금액: 보증금 + 월세 × 100, 그 합이 5천만 원 미만이면 보증금 + 월세 × 70 (시행규칙 제20조⑤1호)
      let base = price;
      let low = false;
      if (lease && rent > 0) {
        base = price + rent * d.rent_times;
        if (base < d.rent_low_below) { base = price + rent * d.rent_times_low; low = true; }
      }

      const dealKey = lease ? 'lease' : 'sale';
      let permil;
      let cap = null;
      if (v.kind === 'house') {
        const band = d.house[dealKey].find((b) => b.below === undefined || base < b.below);
        permil = band.permil;
        cap = band.cap === undefined ? null : band.cap;
      } else if (v.kind === 'officetel') {
        permil = d.officetel[dealKey];
      } else if (v.kind === 'other') {
        permil = d.other_permil;
      } else {
        throw new u.InputError('무엇을 거래하는지 골라 주세요.');
      }

      const raw = u.floor((base * permil) / 1000);
      const fee = cap !== null && raw > cap ? cap : raw;
      const vat = u.floor(fee * d.vat_rate);
      const withVat = fee + vat;

      const rows = [];
      if (lease && rent > 0) {
        rows.push(['거래금액 (환산)', `${u.won(base)} = 보증금 + 월세 × ${low ? d.rent_times_low : d.rent_times}`]);
      } else {
        rows.push(['거래금액', u.won(base)]);
      }
      rows.push(['상한요율', cap !== null ? `${pctText(permil)} (한도액 ${u.won(cap)})` : pctText(permil)]);
      rows.push(['중개보수 상한 (한쪽)', u.won(fee)]);
      rows.push(['부가세 10%를 더하면', u.won(withVat)]);

      const notes = [
        '이 금액은 받을 수 있는 최대치예요. 실제 금액은 이 안에서 중개사와 협의해 정해요.',
        '중개보수는 거래하는 양쪽(파는 사람·사는 사람, 집주인·세입자)이 각각 내요.',
        '경기도 등 지자체 안내는 중개보수의 부가가치세를 별도로 봐요. 간이과세자 사무소는 다를 수 있어 계약 전에 물어보세요.',
      ];
      if (low) notes.push(`보증금 + 월세 × 100이 5천만 원 미만이라 월세에 ${d.rent_times_low}을 곱해 다시 계산했어요.`);
      if (v.kind === 'other') notes.push('토지·상가 등은 거래금액의 0.9% 이내에서 협의해요. 중개사무소에 붙은 요율표의 상한을 넘을 수 없어요.');
      if (v.kind === 'house') notes.push('주택과 상가가 함께 있는 건물은 주택 면적이 절반 이상일 때만 주택 요율이에요.');

      return { values: { base, permil, cap: cap === null ? 0 : cap, fee, with_vat: withVat }, rows, notes };
    },
  });
})();
