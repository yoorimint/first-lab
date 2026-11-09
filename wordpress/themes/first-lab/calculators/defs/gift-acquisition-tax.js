/* 증여 취득세 계산기: 증여(상속 외 무상취득)로 받은 부동산의 시가인정액(지방세법 제10조의2①, 시가표준액 1억 원 이하는 시가표준액 선택 가능 ②2호)
   × 제11조①2호 1천분의 35. 조정대상지역 시가표준액 3억 원 이상 주택(시행령 제28조의6①)은 제13조의2② 4% + 중과기준세율 2%×400% = 12%,
   1세대 1주택자의 주택을 배우자·직계존비속이 받는 경우 등(시행령 제28조의6②)은 제외.
   지방교육세: 제151조①1호 (3.5% − 2%) × 20% = 0.3%, 중과는 같은 호 나목 (4% − 2%) × 20% = 0.4%.
   농어촌특별세: 농어촌특별세법 제5조①6호 (표준세율을 2%로 본 취득세) × 10% = 0.2%, 중과는 (2% + 8%) × 10% = 1.0%,
   국민주택 규모(85㎡) 이하 주택은 제4조 11호 비과세. 10원 미만 버림은 국고금관리법 제47조. 값은 data/gift-acquisition-tax.json (만분율) */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'gift-acquisition-tax',
    title: '증여 취득세 계산기',
    intro: '증여받은 부동산의 시가인정액과 지역·주택 조건을 고르면 취득세·지방교육세·농어촌특별세와 합계가 나와요.',
    inputs: [
      {
        name: 'kind', label: '증여받은 부동산', type: 'select', default: 'house',
        options: [['house', '주택'], ['other', '주택 외 토지·건물 (상가·농지·대지 등)']],
      },
      { name: 'base', label: '내 몫의 과세표준 (원)', type: 'number', min: 0, step: 1000000, help: '보통 시가인정액(증여계약일 전 6개월~후 3개월의 매매·감정·경매·공매가액)이에요. 시가표준액이 1억 원 이하면(지분은 전체 지분의 시가표준액으로 따짐) 시가표준액을 넣어도 돼요. 지분만 받으면 내 지분만큼만 넣어요.' },
      {
        name: 'adjusted', label: '조정대상지역에 있는 주택인가요 (주택만)', type: 'select', default: 'no',
        options: [['no', '아니요'], ['yes', '예']],
        help: '증여계약일 기준 국토교통부 지정 고시로 확인해요.',
      },
      {
        name: 'std3', label: '주택 전체의 시가표준액(공시가격)이 3억 원 이상인가요 (주택만)', type: 'select', default: 'no',
        options: [['no', '아니요 (3억 원 미만)'], ['yes', '예 (3억 원 이상)']],
        help: '지분만 받아도 주택 전체의 공시가격으로 봐요.',
      },
      {
        name: 'exempt', label: '1세대 1주택자가 그 집을 배우자·직계존비속에게 주나요 (주택만)', type: 'select', default: 'no',
        options: [['no', '아니요'], ['yes', '예']],
        help: '주는 사람 세대가 이 집 1채만 가진 경우예요. 사실혼 배우자는 빠져요.',
      },
      {
        name: 'area', label: '주택 전용면적 (주택만)', type: 'select', default: 'small',
        options: [['small', '85㎡ 이하'], ['large', '85㎡ 초과']],
        help: '수도권 밖 도시지역이 아닌 읍·면은 100㎡ 이하까지 농어촌특별세가 붙지 않으니 100㎡ 이하면 "85㎡ 이하"를 골라요.',
      },
    ],

    compute(v, d, u) {
      const base = Number(v.base);
      if (!(base > 0)) throw new u.InputError('과세표준을 넣으면 결과가 나와요.');
      if (!Number.isInteger(base)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (base > d.max_input) throw new u.InputError('금액이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      const kind = v.kind;
      if (!['house', 'other'].includes(kind)) throw new u.InputError('부동산 종류를 골라 주세요.');
      const house = kind === 'house';
      const heavy = house && v.adjusted === 'yes' && v.std3 === 'yes' && v.exempt !== 'yes';

      const add = d.base_rate * d.heavy_times; // 중과기준세율의 400% = 8%
      const rate = heavy ? d.heavy_std_rate + add : d.gift_rate; // 제13조의2② / 제11조①2호
      const eduRate = (((heavy ? d.heavy_std_rate : d.gift_rate) - d.base_rate) * d.edu_pct) / 100; // 제151조①1호 (나목)
      let ruralRate = ((heavy ? d.base_rate + add : d.base_rate) * d.rural_pct) / 100; // 농특세법 제5조①6호
      let ruralWhy = '';
      if (house && v.area !== 'large') { ruralRate = 0; ruralWhy = '국민주택 규모 이하 주택 비과세'; }

      const f10 = u.floor10;
      const tax = f10((base * rate) / 10000);
      const edu = f10((base * eduRate) / 10000);
      const rural = f10((base * ruralRate) / 10000);
      const total = tax + edu + rural;
      const pct = (r) => `${(r / 100).toFixed(2).replace(/\.?0+$/, '')}%`;

      const rows = [
        ['적용 세율', heavy ? `조정대상지역 3억 원 이상 주택 중과 (취득세 ${pct(rate)})` : `증여 기본세율 (취득세 ${pct(rate)})`],
        ['취득세', u.won(tax)],
        ['지방교육세', `${u.won(edu)} (${pct(eduRate)})`],
        ['농어촌특별세', rural ? `${u.won(rural)} (${pct(ruralRate)})` : `0원 (${ruralWhy})`],
        ['합계', `${u.won(total)} (세율 합계 ${pct(rate + eduRate + ruralRate)})`],
      ];

      const notes = [
        '증여계약일이 속한 달의 말일부터 3개월 안에 신고·납부해요. 그 전에 등기하면 등기 신청일까지 내야 해요.',
        '증여세는 따로 내요. 빚을 함께 넘기는 부담부증여는 채무 부분에 매매 세율이 붙어 이 계산과 달라요.',
        '고급주택·고급오락장 같은 사치성 재산은 세율에 8%가 더 붙고, 이혼 재산분할은 특례로 1.5%라 이 계산과 달라요.',
      ];
      if (house && v.adjusted === 'yes' && v.std3 === 'yes' && v.exempt === 'yes') {
        notes.push('1세대 1주택자의 집을 배우자·직계존비속이 받는 경우라 12% 중과 없이 3.5%로 계산했어요.');
      }
      if (heavy) {
        notes.push('조정대상지역의 시가표준액 3억 원 이상 주택이라 12%로 계산했어요. 주는 사람이 1세대 1주택자이고 받는 사람이 배우자·직계존비속이면 3.5%예요.');
      }
      return { values: { rate, tax, edu, rural, total }, rows, notes };
    },
  });
})();
