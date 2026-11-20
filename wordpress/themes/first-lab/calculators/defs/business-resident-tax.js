/* 주민세 사업소분 계산기: 기본세율(개인 5만 원, 법인 자본금별 5만·10만·20만 원) + 연면적분(330㎡ 넘으면 연면적 전체 × ㎡당 250원, 오염물질 배출 사업소 500원)
 * + 지방교육세(기본세율분의 10%, 인구 50만 이상 시 25%). 지방세법 제75조②·제81조①·제82조·제151조①5, 시행령 제79조②.
 * 조례로 세율을 50% 범위에서 가감할 수 있음(제81조②) — 이 계산은 법에 정한 표준 세율. 요율은 data/business-resident-tax.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'business-resident-tax',
    title: '주민세 사업소분 계산기',
    intro: '사업장을 가진 사업자가 8월에 내는 주민세 사업소분을 계산해요. 사업자 종류, 작년 매출, 사업장 넓이, 지역을 넣으면 지방교육세까지 나와요.',
    inputs: [
      {
        name: 'owner', label: '사업자는 누구인가요?', type: 'choice', default: 'person',
        options: [['person', '개인사업자'], ['corp_s', '법인 자본금 30억 이하'], ['corp_m', '법인 30억 초과~50억'], ['corp_l', '법인 50억 초과']],
        help: '자본금(출자금)이 없는 그 밖의 법인은 \'법인 자본금 30억 이하\'를 골라요. 둘 다 5만 원이에요.',
      },
      {
        name: 'sales', label: '작년 매출이 8천만 원 이상인가요?', type: 'choice', default: 'yes',
        options: [['yes', '8천만 원 이상'], ['no', '8천만 원 미만']],
        help: '개인사업자만 봐요. 직전 연도 부가가치세 과세표준이에요. 면세사업자는 총수입금액으로 봐요. 담배소매인·연탄과 양곡 소매인·노점상인·유치원과 어린이집 경영자는 다른 업종을 함께 하지 않으면 내지 않아요.',
      },
      { name: 'area', label: '사업장 연면적 (㎡)', type: 'number', min: 0, step: 0.01, default: 100, help: '7월 1일 기준 사업소용 건물 넓이예요. 서울시는 공용면적을 포함해 계산한다고 안내해요.' },
      {
        name: 'pollution', label: '오염물질 배출 사업소인가요?', type: 'choice', default: 'no',
        options: [['no', '아니요'], ['yes', '예 (㎡당 500원)']],
        help: '폐수나 사업장폐기물 등을 배출하는 사업소로 법이 정한 곳이에요.',
      },
      {
        name: 'city', label: '사업장이 인구 50만 이상 시인가요?', type: 'choice', default: 'big',
        options: [['big', '예 (교육세 25%)'], ['small', '아니요 (교육세 10%)']],
        help: '서울시는 사업소분 기본세액의 25%를 지방교육세로 고지한다고 안내해요. 도농복합시는 동 지역 인구로 따지고, 그 시의 읍·면 지역은 10%예요.',
      },
    ],

    compute(v, d, u) {
      const area = Number(v.area);
      if (!(area >= 0) || v.area === '' || v.area === undefined) throw new u.InputError('사업장 연면적을 넣으면 결과가 나와요. 모르면 0을 넣어요.');
      if (area > d.max_area) throw new u.InputError('연면적이 너무 커요. 1천만 ㎡ 이하로 넣어 주세요.');
      // 시행령 제82조: 연면적 중 1㎡ 미만은 계산하지 않음 (11/20 외부 검수 — 330.01㎡ 를 330㎡ 넘는 것으로 봐 연면적분을 매겼음)
      const taxArea = u.floor(area);
      const person = v.owner === 'person';
      const exempt = person && v.sales === 'no';

      let base = 0;
      let areaTax = 0;
      let eduTax = 0;
      if (!exempt) {
        base = d.base[v.owner];
        if (base === undefined) throw new u.InputError('사업자 종류를 골라 주세요.');
        if (taxArea > d.area_free_max) areaTax = u.floor10(taxArea * (v.pollution === 'yes' ? d.area_rate_pollution : d.area_rate));
        eduTax = u.floor10((base * (v.city === 'small' ? d.edu_pct_small : d.edu_pct_big)) / 100);
      }
      const total = base + areaTax + eduTax;

      const values = { exempt, base, area_tax: areaTax, edu_tax: eduTax, total };
      const rows = exempt
        ? [['주민세 사업소분', '내지 않아요 (작년 매출 8천만 원 미만 개인사업자)']]
        : [
            ['기본세율분', u.won(base)],
            ['연면적분', taxArea > d.area_free_max ? `${u.won(areaTax)} (${taxArea.toLocaleString('ko-KR')}㎡ × ${v.pollution === 'yes' ? d.area_rate_pollution : d.area_rate}원)` : '0원 (330㎡ 이하)'],
            [`지방교육세 (기본세율분의 ${v.city === 'small' ? d.edu_pct_small : d.edu_pct_big}%)`, u.won(eduTax)],
            ['8월에 낼 금액 (합계)', u.won(total)],
          ];
      const notes = [];
      if (exempt) {
        notes.push('개인사업자는 작년 매출(부가가치세 과세표준)이 8천만 원 이상일 때만 사업소분을 내요.');
      } else {
        notes.push('8월 1일부터 31일까지 사업장이 있는 지방자치단체에 신고하고 내요. 위택스(wetax.go.kr)에서 할 수 있어요.');
        if (taxArea <= d.area_free_max) notes.push('연면적이 330㎡ 이하라 연면적분은 붙지 않아요.');
        else notes.push('연면적이 330㎡를 넘으면 넘는 부분만이 아니라 연면적 전체에 붙어요.');
        notes.push('지방교육세는 기본세율분에만 붙고 연면적분에는 붙지 않아요.');
      }
      notes.push('지방자치단체 조례로 기본세율과 ㎡당 250원을 50% 범위에서 올리거나 내릴 수 있어요. 이 계산은 법에 정한 표준 세율이라 고지서와 다르면 고지서 금액을 따라요.');
      notes.push('7월 1일 기준 1년 이상 계속 휴업 중이면 내지 않아요. 10원 미만은 버렸어요.');
      return { values, rows, notes };
    },
  });
})();
