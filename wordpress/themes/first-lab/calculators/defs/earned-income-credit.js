/* 근로장려금 계산기: 총급여액 등(근로소득 총급여 + 사업수입금액 × 업종별 조정률)을 별표 11 근로장려금 산정표에서 찾고,
   재산 1억 7천만 원 이상이면 50%, 기한 후 신청이면 95%, 결정 최소액(1만 5천 원 미만 0원, 늘어나는 구간 10만 원, 줄어드는 구간 3만 원)을 적용.
   기준은 data/earned-income-credit.json (조세특례제한법 제100조의3·제100조의5·제100조의7, 시행령 제100조의3·별표 11) */
(function () {
  'use strict';

  const TYPE_NAME = { single: '단독 가구', one: '홑벌이 가구', dual: '맞벌이 가구' };
  const TYPE_COL = { single: 2, one: 3, dual: 4 };

  FirstLabCalc.register({
    id: 'earned-income-credit',
    title: '근로장려금 계산기',
    intro: '가구 유형과 한 해 소득·재산을 넣으면 법에 정한 근로장려금 산정표대로 예상 지급액이 나와요.',
    inputs: [
      {
        name: 'household', label: '가구 유형', type: 'select', default: 'single',
        options: [
          ['single', '단독 가구 (배우자·부양자녀·70세 이상 부모 없음)'],
          ['one', '홑벌이 가구 (배우자 소득 300만 원 미만, 또는 배우자 없이 부양자녀·70세 이상 부모 있음)'],
          ['dual', '맞벌이 가구 (본인·배우자 총급여액 등이 각각 300만 원 이상)'],
        ],
        help: '부양자녀는 18세 미만이고 연 소득금액 100만 원 이하여야 해요. 부모는 70세 이상, 각각 연 소득금액 100만 원 이하이고 같이 살아야 해요. 법에서 정한 중증장애인 등은 나이 제한이 없어요.',
      },
      { name: 'wage', label: '한 해 근로소득 (원, 비과세 빼고, 부부 합산)', type: 'number', min: 0, step: 1, help: '원천징수영수증의 총급여예요. 일용직 일당도 더해요. 배우자 소득도 합쳐요.' },
      { name: 'biz', label: '한 해 사업 수입금액 (원, 없으면 비워 둬요)', type: 'number', min: 0, step: 1, help: '프리랜서 3.3% 소득은 떼기 전 금액이에요. 배우자 사업 수입도 업종이 같으면 합쳐요.' },
      {
        name: 'biz_type', label: '사업 업종 (조정률)', type: 'select', default: 'r90',
        options: [
          ['r90', '인적용역(프리랜서 3.3%)·임대업(부동산 제외)·가구 내 고용활동 (90%)'],
          ['rent', '부동산임대업 (총소득 기준에만 90%로 들어가고 금액 계산에서는 빠짐)'],
          ['r75', '전문·과학·기술서비스, 교육서비스, 보건·사회복지서비스, 부동산 관련 서비스 (75%)'],
          ['r70', '금융업, 예술·스포츠·여가 서비스, 수리·기타 개인서비스 (70%)'],
          ['r60', '상품중개, 컴퓨터·정보서비스, 보험·연금업 (60%)'],
          ['r55', '주점업, 숙박업, 운수업, 출판·영상·방송통신업 (55%)'],
          ['r45', '건설업, 전기·가스·수도사업 (45%)'],
          ['r40', '제조업, 음식점업(주점 제외), 부동산매매업 (40%)'],
          ['r30', '광업, 자동차·부품 판매업, 그 밖의 사업 (30%)'],
          ['r25', '소매업, 농업·임업·어업 (25%)'],
          ['r20', '도매업 (20%)'],
        ],
        help: '사업 수입금액에 이 비율을 곱한 금액만 소득으로 쳐요.',
      },
      { name: 'property', label: '가구원 재산 합계 (원)', type: 'number', min: 0, step: 1, help: '그해 6월 1일 기준 집·땅·자동차·전세금·예금 등을 모두 더한 금액이에요.' },
      {
        name: 'timing', label: '신청 시기', type: 'select', default: 'regular',
        options: [['regular', '정기 신청 (5월)'], ['late', '기한 후 신청 (6월~11월, 95% 지급)']],
      },
    ],

    compute(v, d, u) {
      const num = (x, label, eun, ga) => {
        if (x === undefined || x === null || x === '') return 0;
        const n = Number(x);
        if (!(n >= 0) || !Number.isInteger(n)) throw new u.InputError(`${label}${eun} 0 이상 1원 단위 정수로 넣어 주세요.`);
        if (n > d.max_input) throw new u.InputError(`${label}${ga} 너무 커요. 100억 원 이하로 넣어 주세요.`);
        return n;
      };
      const type = v.household;
      if (!TYPE_NAME[type]) throw new u.InputError('가구 유형을 골라 주세요.');
      const wage = num(v.wage, '근로소득', '은', '이');
      const biz = num(v.biz, '사업 수입금액', '은', '이');
      const property = num(v.property, '재산 합계', '는', '가');
      if (wage + biz <= 0) throw new u.InputError('근로소득이나 사업 수입금액을 넣으면 결과가 나와요.');
      const rent = biz > 0 && v.biz_type === 'rent';
      const rate = biz > 0 ? d.biz_rates[rent ? 'r90' : v.biz_type] : 0;
      if (rate === undefined) throw new u.InputError('사업 업종을 골라 주세요.');
      const late = v.timing === 'late';

      // 사업소득 = 수입금액 × 조정률 (시행령 제100조의3), 원 미만 버림
      const bizIncome = u.floor((biz * rate) / 100);
      // 부동산임대 소득은 총급여액 등에서 빠지고(시행령 제100조의6②5호) 총소득 기준 판정에만 들어감(시행령 제100조의3①4호차목)
      const total = wage + (rent ? 0 : bizIncome);
      const totalIncome = wage + bizIncome;
      const limit = d.income_limit[type];
      const rows = [];
      if (biz > 0) {
        rows.push(['근로소득', u.won(wage)]);
        rows.push([rent ? `부동산임대 소득 (수입 ${u.won(biz)} × ${rate}%, 총소득 기준에만)` : `사업소득 (수입 ${u.won(biz)} × ${rate}%)`, u.won(bizIncome)]);
      }
      rows.push(['총급여액 등', u.won(total)]);
      const notes = [];
      let tableAmount = 0;
      let reason = '';
      if (totalIncome >= limit) {
        reason = `${TYPE_NAME[type]}는 총소득이 ${u.won(limit)} 미만이어야 해요.`;
      } else if (property >= d.property_limit) {
        reason = `가구원 재산 합계가 ${u.won(d.property_limit)} 미만이어야 해요.`;
      } else {
        const col = TYPE_COL[type];
        const row = d.table.find((r) => r[0] <= total && total < r[1]);
        tableAmount = row && row[col] ? row[col] : 0;
        if (row) rows.push(['산정표 구간', `${u.won(row[0])} 이상 ${u.won(row[1])} 미만`]);
        if (!tableAmount) reason = type === 'dual' && total < d.dual_each_min * 2
          ? '맞벌이 가구는 부부 각각 300만 원 이상이어야 해서, 합계가 600만 원보다 적으면 맞벌이로 볼 수 없어요.'
          : '산정표에서 이 소득의 근로장려금이 없어요.';
      }

      let amount = tableAmount;
      if (tableAmount) {
        rows.push([`산정표 금액 (${TYPE_NAME[type]})`, u.won(tableAmount)]);
        if (property >= d.property_half) {
          amount = (amount * d.half_pct) / 100;
          rows.push([`재산 ${u.won(d.property_half)} 이상 (${d.half_pct}%)`, u.won(amount)]);
        }
        if (late) {
          amount = (amount * d.late_pct) / 100;
          rows.push([`기한 후 신청 (${d.late_pct}%)`, u.won(amount)]);
        }
        const phaseIn = total < d.phase_in_end[type];
        const phaseOut = total >= d.flat_end[type];
        if (amount < d.zero_below) {
          amount = 0;
          reason = `계산한 금액이 ${u.won(d.zero_below)} 미만이면 지급하지 않아요.`;
        } else if (phaseIn && amount < d.phase_in_min) {
          amount = d.phase_in_min;
          notes.push(`소득이 늘수록 금액이 늘어나는 구간에서 계산한 금액이 ${u.won(d.zero_below)} 이상 ${u.won(d.phase_in_min)} 미만이면 ${u.won(d.phase_in_min)}을 줘요.`);
        } else if (phaseOut && amount < d.phase_out_min) {
          amount = d.phase_out_min;
          notes.push(`소득이 늘수록 금액이 줄어드는 구간에서 계산한 금액이 ${u.won(d.zero_below)} 이상 ${u.won(d.phase_out_min)} 미만이면 ${u.won(d.phase_out_min)}을 줘요.`);
        }
        amount = Math.round(amount);
      }
      if (reason) notes.unshift(reason);
      rows.push(['예상 근로장려금', u.won(amount)]);

      notes.push(`${d.income_year}년 소득분(다음 해 5월 정기 신청) 기준이에요. 실제 금액은 국세청 심사로 정해져요.`);
      notes.push('자격은 이자·배당·연금·기타소득까지 더한 총소득으로 따져요. 그 합계가 기준 이상이면 받을 수 없어요.');
      notes.push('본인·배우자가 전문직 사업자이거나, 연말에 계속 근무 중인 상용근로자로서 월평균 근로소득이 500만 원 이상이면 신청할 수 없어요.');
      if (type === 'one' || type === 'dual') notes.push('배우자 소득도 넣은 금액에 합쳐야 해요.');
      if (rent) notes.push('부동산임대 소득은 근로장려금 금액을 정하는 총급여액 등에서 빠지고, 받을 자격(총소득 기준)을 따질 때만 더해요.');
      if (biz > 0) notes.push('사업자등록 없이 번 사업소득(일부 인적용역 제외), 본인·배우자의 직계존비속에게서 받은 원천징수 대상 소득 등은 총급여액 등에서 빠져요.');
      if (late) notes.push('기한 후 신청은 정기 신청기간이 끝난 다음 날부터 6개월 안에만 할 수 있어요.');
      const values = { biz_income: bizIncome, total, total_income: totalIncome, table_amount: tableAmount, amount, eligible: amount > 0 };
      return { values, rows, notes };
    },
  });
})();
