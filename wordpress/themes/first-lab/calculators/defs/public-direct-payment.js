/* 공익직불금(기본형 면적직불금) 계산기: 농지 종류별 면적 → 면적직불금, 소농직불금 해당 여부 안내. 단가는 data/public-direct-payment.json
 * 근거: 농업ㆍ농촌 공익기능 증진 직접지불제도 운영에 관한 법률 제9조③·제10조·제11조, 같은 법 시행령 제8조·제9조·제11조·별표 1의2·별표 2,
 *       농림축산식품부고시 제2025-3244호(면적직접지불금의 기준면적 구간별 단가)
 * 별표 2: 진흥지역 논·밭 → 비진흥 논 → 비진흥 밭 순서로 면적을 쌓아 구간(2만㎡ 이하·6만㎡ 이하·초과)을 정하고, 구간별 면적 × 단가를 더함.
 * 단가는 1만㎡당 만 원 → 1㎡당 원과 같은 숫자 (215만 원/1만㎡ = 215원/㎡). */
(function () {
  'use strict';

  const KINDS = [
    ['zone', '농업진흥지역 논·밭'],
    ['paddy', '진흥지역 밖 논'],
    ['field', '진흥지역 밖 밭'],
  ];

  FirstLabCalc.register({
    id: 'public-direct-payment',
    title: '공익직불금 계산기 (면적직불금)',
    intro: '농지 종류별 면적(㎡)을 넣으면 기본형 공익직불금 중 면적직불금이 얼마인지 나와요. 소농직불금 대상인지도 함께 알려 줘요.',
    inputs: [
      { name: 'zone', label: '농업진흥지역 안 논·밭 면적 (㎡)', type: 'number', min: 0, step: 1, default: 0, help: '농지대장이나 등록 면적 기준이에요. 1ha는 10,000㎡예요.' },
      { name: 'paddy', label: '농업진흥지역 밖 논 면적 (㎡)', type: 'number', min: 0, step: 1, default: 0 },
      { name: 'field', label: '농업진흥지역 밖 밭 면적 (㎡)', type: 'number', min: 0, step: 1, default: 0 },
      {
        name: 'who', label: '받는 사람', type: 'select', default: 'farmer',
        options: [['farmer', '농업인'], ['corp', '농업법인 (공동농업경영체 아님)']],
      },
    ],

    compute(v, d, u) {
      const area = {};
      for (const [key] of KINDS) {
        const raw = String(v[key] ?? '').trim();
        const n = raw === '' ? 0 : Number(raw);
        if (!(n >= 0) || !Number.isFinite(n)) throw new u.InputError('면적은 0 이상의 숫자(㎡)로 넣어 주세요.');
        area[key] = n;
      }
      const total = area.zone + area.paddy + area.field;
      if (!(total > 0)) throw new u.InputError('농지 면적을 하나 이상 넣으면 결과가 나와요.');
      const cap = v.who === 'corp' ? d.cap_corp_m2 : d.cap_farmer_m2;
      const notes = [];
      const rows = [];

      // 상한을 넘는 면적은 순서상 뒤(비진흥 밭 → 비진흥 논 → 진흥) 면적부터 빠짐 — 별표 2의 쌓는 순서로 상한까지만 계산
      let used = 0;
      let pay = 0;
      const parts = [];
      for (const [key, label] of KINDS) {
        const counted = Math.min(area[key], Math.max(cap - used, 0));
        let left = counted;
        let part = 0;
        while (left > 1e-9) {
          const tier = d.tiers_m2.findIndex((lim) => used < lim - 1e-9);
          const t = tier === -1 ? d.tiers_m2.length : tier;
          const room = t < d.tiers_m2.length ? d.tiers_m2[t] - used : left;
          const take = Math.min(left, room);
          part += take * d.unit_won_per_m2[key][t];
          used += take;
          left -= take;
        }
        if (area[key] > 0) {
          const cut = area[key] - counted; // 상한을 넘어 계산에서 뺀 면적은 줄 이름에 밝힘 (11/12 검수: 31만㎡ 줄에 30만㎡분 금액)
          parts.push([`${label} ${counted.toLocaleString('ko-KR')}㎡${cut > 0 ? ` (상한 넘는 ${cut.toLocaleString('ko-KR')}㎡ 제외)` : ''}`, u.floor(part)]);
        }
        pay += part;
      }
      pay = u.floor(pay);
      let reason = '';
      if (total < d.min_area_m2) {
        reason = `농업에 쓰는 농지 면적(휴경 제외)이 ${d.min_area_m2.toLocaleString('ko-KR')}㎡ 미만이면 기본형 공익직불금을 받을 수 없어요.`;
        pay = 0;
      }
      // 대상이 아니면 줄 금액도 0원 (11/12 검수: 999㎡에 줄은 149,850원, 합계는 0원)
      for (const [name, amount] of parts) rows.push([name, u.won(reason ? 0 : amount)]);
      rows.push(['면적직불금 합계', u.won(pay)]);
      // 상한을 넘는 면적을 어느 농지에서 빼는지는 법령에 없음 (11/12 2차 검수 확인불가) → 섞어 넣고 넘으면 안내
      if (total > cap && KINDS.filter(([key]) => area[key] > 0).length > 1) notes.push('상한을 넘는 면적을 어느 농지에서 빼는지는 이 계산기가 단가가 낮은 농지부터 뺀 값이에요. 실제 지급 면적은 읍·면·동에 확인하세요.');
      if (total > cap) notes.push(`면적직불금은 ${(cap).toLocaleString('ko-KR')}㎡까지만 줘서, 넘는 면적은 빼고 계산했어요 (2019년에 그보다 넓게 받은 경우 등 예외는 읍·면·동에 확인).`);

      let small = 'none';
      if (!reason && v.who === 'farmer') {
        if (total <= d.small_area_m2) small = 'auto';
        else if (pay < d.small_pay) small = 'choice';
      }
      if (small === 'auto') {
        notes.push(`농가 전체 지급대상 면적이 ${d.small_area_m2.toLocaleString('ko-KR')}㎡ 이하예요. 영농·농촌 거주 3년, 소득 기준 같은 소농 조건을 모두 갖추면 면적직불금 대신 농가당 ${u.won(d.small_pay)}(소농직불금)을 받아요.`);
      } else if (small === 'choice') {
        notes.push(`면적직불금이 ${u.won(d.small_pay)}보다 적어요. 농가 구성원 소유 농지가 ${d.small_own_m2.toLocaleString('ko-KR')}㎡ 미만이고 나머지 소농 조건을 갖추면 소농직불금을 골라 신청할 수 있어요.`);
      }
      if (v.who === 'corp') notes.push('농업법인은 소농직불금을 받을 수 없어요.');
      if (reason) notes.push(reason);
      notes.push('진흥지역 논·밭 → 진흥지역 밖 논 → 진흥지역 밖 밭 순서로 면적을 더해 2만㎡·6만㎡ 구간을 나누고, 구간마다 단가를 곱했어요. 준수사항 위반 감액은 빼지 않았어요.');
      return { values: { pay, small }, rows, notes };
    },
  });
})();
