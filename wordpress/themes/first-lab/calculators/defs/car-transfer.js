/* 자동차 명의이전 계산기: 취득세(차종·취득 원인별)와 이전등록 지연 범칙금. 요율은 data/car-transfer.json */
(function () {
  'use strict';

  /**
   * 이전등록 신청 지연 범칙금 (자동차관리법 시행령 별표 3 제3호):
   * 지연 10일 이내 10만원 / 10일 초과 49일 이내는 10만원에 11일째부터 1일마다 1만원 / 50일 이상 50만원
   */
  function penaltyFor(days, p) {
    if (days <= 0) return 0;
    if (days <= p.base_days) return p.base;
    if (days >= p.max_from_day) return p.max;
    return p.base + p.per_day * (days - p.base_days);
  }

  FirstLabCalc.register({
    id: 'car-transfer',
    title: '자동차 명의이전 계산기',
    intro: '차종과 거래가격을 넣으면 명의이전 때 내는 취득세를, 신청 기한을 넘겼다면 지연 범칙금까지 계산해요.',
    inputs: [
      {
        name: 'cause', label: '이전 사유', type: 'select', default: 'sale',
        options: [['sale', '매매 (돈을 주고 삼)'], ['gift', '증여 (무상으로 받음)']],
      },
      {
        name: 'kind', label: '차종 (개인 비영업용)', type: 'select', default: 'car',
        options: [
          ['car', '승용차'],
          ['light_car', '경형 승용차'],
          ['van_truck', '승합·화물차'],
          ['light_van_truck', '경형 승합·화물차'],
        ],
      },
      { name: 'price', label: '거래가격 (원)', type: 'number', min: 0, step: 10000, help: '매매계약서에 적은 실제 거래가격이에요. 증여면 비워 둬도 돼요.' },
      { name: 'standard', label: '시가표준액 (원, 선택)', type: 'number', min: 0, step: 10000, help: '위택스나 자동차365 취득세 신고 화면에서 볼 수 있어요. 증여는 이 값이 필요해요.' },
      { name: 'late_days', label: '신청 기한을 넘긴 날 수', type: 'number', min: 0, step: 1, default: 0, help: '기한 안이면 0. 매매는 매수한 날부터 15일, 증여는 받은 날부터 20일 안에 신청해요.' },
    ],

    compute(v, d, u) {
      const rule = d.tax[v.kind];
      if (!rule) throw new u.InputError('차종을 골라 주세요.');
      const price = Number(v.price) || 0;
      const standard = Number(v.standard) || 0;
      const lateDays = Number(v.late_days) || 0;
      if (!Number.isInteger(lateDays) || lateDays < 0) throw new u.InputError('기한을 넘긴 날 수는 0 이상의 정수로 넣어 주세요.');

      let base;
      if (v.cause === 'gift') {
        if (!(standard > 0)) throw new u.InputError('증여는 시가표준액으로 세금을 매겨요. 시가표준액을 넣어 주세요.');
        base = standard;
      } else if (v.cause === 'sale') {
        if (!(price > 0)) throw new u.InputError('거래가격을 넣으면 결과가 나와요.');
        base = Math.max(price, standard); // 신고가가 시가표준액보다 적으면 시가표준액
      } else {
        throw new u.InputError('이전 사유를 골라 주세요.');
      }

      const taxFree = base <= d.tax_free_max; // 지방세법 제17조 면세점: 취득가액 50만 원 이하 비과세
      const gross = taxFree ? 0 : Math.floor((base * Math.round(rule.rate * 1000)) / 1000); // 1천분의 N 을 정수로 계산
      // 면제라도 산출세액이 200만 원을 넘으면 85%만 감면 (지방세특례제한법 제177조의2 최소납부세제)
      const relief = rule.exempt
        ? (gross <= d.min_tax.free_max ? gross : Math.floor((gross * Math.round(d.min_tax.relief_rate * 100)) / 100))
        : Math.min(gross, rule.relief || 0);
      const tax = gross - relief;
      const penalty = penaltyFor(lateDays, d.penalty);

      const rows = [
        ['과세표준', u.won(base)],
        ['세율', `${(rule.rate * 100).toFixed(0)}%`],
        ['산출 취득세', u.won(gross)],
      ];
      if (rule.relief || rule.exempt) rows.push(['경차 감면', relief ? `-${u.won(relief)}` : '없음']);
      rows.push(['낼 취득세', tax ? u.won(tax) : '0원 (면제)']);
      rows.push(['지연 범칙금', penalty ? u.won(penalty) : '없음']);
      rows.push(['합계 (취득세 + 범칙금)', u.won(tax + penalty)]);

      const notes = [
        '매매는 계약서의 거래가격이 시가표준액보다 낮으면 시가표준액으로 세금을 매겨요.',
        '공채 매입, 증지·인지, 번호판 값은 지역·차종마다 달라 여기에 넣지 않았어요.',
        '실제 고지 금액은 원 단위 처리 등으로 조금 다를 수 있어요.',
      ];
      if (rule.relief) notes.push(`경형 승용차는 취득세 ${u.won(rule.relief)}까지 감면돼요 (${d.relief_until}까지).`);
      if (taxFree) notes.push('과세표준이 50만 원 이하라 취득세가 없어요 (지방세법 제17조).');
      if (rule.exempt) notes.push(`경형 승합·화물차는 취득세가 면제돼요 (${d.relief_until}까지).`);

      return { values: { base, gross, relief, tax, penalty, total: tax + penalty }, rows, notes };
    },
  });
})();
