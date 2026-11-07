/* 산재보험료 계산기: 보수 × (사업종류별 산재보험료율 + 출퇴근재해 요율). 요율은 data/industrial-accident-insurance.json
   (고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률 제13조⑤·제14조③⑦, 고용노동부 고시 2026년도 사업종류별 산재보험료율) */
(function () {
  'use strict';

  const INDUSTRY = [
    ['retail_food', '도소매·음식·숙박업'],
    ['professional', '전문·보건·교육·여가관련 서비스업'],
    ['finance', '금융 및 보험업'],
    ['real_estate', '부동산 및 임대업'],
    ['facility', '시설관리 및 사업지원 서비스업'],
    ['misc', '기타의 각종사업'],
    ['government', '국가 및 지방자치단체의 사업'],
    ['construction', '건설업'],
    ['rail_air', '철도·항공·창고·운수관련서비스업'],
    ['land_water', '육상 및 수상운수업'],
    ['telecom', '통신업'],
    ['utility', '전기·가스·증기·수도사업'],
    ['food', '식료품 제조업'],
    ['textile', '섬유 및 섬유제품 제조업'],
    ['wood_paper', '목재 및 종이제품 제조업'],
    ['printing', '출판·인쇄·제본업'],
    ['chemical', '화학 및 고무제품 제조업'],
    ['pharma', '의약품·화장품·연탄·석유제품 제조업'],
    ['machinery', '기계기구·금속·비금속광물제품 제조업'],
    ['smelting', '금속제련업'],
    ['electronics', '전기기계기구·정밀기구·전자제품 제조업'],
    ['shipbuilding', '선박건조 및 수리업'],
    ['handmade', '수제품 및 기타제품 제조업'],
    ['coal_mining', '석탄광업 및 채석업'],
    ['other_mining', '석회석·금속·비금속·기타광업'],
    ['forestry', '임업'],
    ['fishery', '어업'],
    ['agriculture', '농업'],
  ];
  const NAME = Object.fromEntries(INDUSTRY);
  const permil = (n) => `${Math.round(n * 10) / 10}/1,000 (${Math.round(n * 100) / 1000}%)`;

  FirstLabCalc.register({
    id: 'industrial-accident-insurance',
    title: '산재보험료 계산기',
    intro: '업종과 보수를 넣으면 올해 사업종류별 산재보험료율에 출퇴근재해 요율을 더해 회사가 낼 산재보험료를 계산해요.',
    inputs: [
      { name: 'industry', label: '사업 종류', type: 'select', default: 'retail_food', options: INDUSTRY, help: '사업종류 예시표에 따라 분류된 우리 사업장의 사업 종류예요. 사업자등록증 업종과 다를 수 있어서 근로복지공단에 확인해요.' },
      { name: 'wage', label: '보수 (원)', type: 'number', min: 0, step: 1, default: 3000000, help: '비과세 소득을 뺀 근로자 보수예요. 한 달 치를 넣으면 월 보험료, 1년 치(보수총액)를 넣으면 1년 보험료가 나와요.' },
      {
        name: 'commute', label: '출퇴근재해 요율', type: 'select', default: 'yes',
        options: [['yes', '더함 (대부분의 사업장)'], ['no', '빼기 (출퇴근재해 적용 제외 대상)']],
        help: '출퇴근 경로가 일정하지 않은 직종으로 정해진 경우(산업재해보상보험법 제37조④)만 빼요.',
      },
    ],

    compute(v, d, u) {
      const wage = Number(v.wage);
      if (!(wage > 0)) throw new u.InputError('보수를 넣으면 결과가 나와요.');
      if (!Number.isInteger(wage)) throw new u.InputError('보수는 1원 단위 정수로 넣어 주세요.');
      const base = d.rates_permil[v.industry];
      if (base === undefined) throw new u.InputError('사업 종류를 골라 주세요.');
      const commute = v.commute === 'no' ? 0 : d.commute_permil;
      const total10 = Math.round(base * 10) + Math.round(commute * 10); // 만분율(1/10,000) 정수로 더해 소수 오차를 없앰
      const premium = FirstLabCalc.util.floor((wage * total10) / 10000);
      const notes = [
        '근로자의 산재보험료는 회사(사업주)가 전부 내요. 근로자 월급에서 떼지 않아요.',
        '원 미만은 버렸어요. 실제 고지액은 근로복지공단 계산과 몇 원 다를 수 있어요.',
        '보험관계 성립 후 3년이 지난 일정 규모 이상 사업은 개별실적요율로 요율이 오르내릴 수 있어요. 우리 사업장 요율은 근로복지공단에 확인해요.',
      ];
      if (v.commute === 'no') notes.push('출퇴근재해 요율을 뺐어요. 근로자를 쓰는 일반 사업장은 대부분 더해요.');
      if (v.industry === 'construction') notes.push('건설업(건설장비운영업 제외)은 매달 고지하지 않고 개산·확정보험료로 1년 치를 신고·납부해요.');
      if (v.industry === 'forestry') notes.push('임업 중 벌목업은 매달 고지하지 않고 개산·확정보험료로 1년 치를 신고·납부해요.');
      return {
        values: { base_permil: base, commute_permil: commute, total_permil: total10 / 10, premium },
        rows: [
          ['사업 종류', NAME[v.industry]],
          [`사업종류별 요율 (${d.year}년)`, permil(base)],
          ['출퇴근재해 요율', commute ? permil(commute) : '빼기'],
          ['합계 요율', permil(total10 / 10)],
          ['보수', u.won(wage)],
          ['산재보험료 (회사 부담)', u.won(premium)],
        ],
        notes,
      };
    },
  });
})();
