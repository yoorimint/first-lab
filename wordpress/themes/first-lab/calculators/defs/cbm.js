/* CBM 계산기: 상자 가로·세로·높이 × 수량 → CBM(㎥)·세제곱피트, 우체국 EMS 부피중량(가로×세로×높이(cm)÷6000)과 적용 중량.
 * 단위 환산·EMS 나눗수는 data/cbm.json */
(function () {
  'use strict';

  const UNITS = [
    ['cm', '센티미터 (cm)'],
    ['mm', '밀리미터 (mm)'],
    ['m', '미터 (m)'],
    ['inch', '인치 (inch)'],
  ];

  // 소수 오차 없이 반올림 (0.096 × 10 = 0.9600000000000001 같은 값 정리)
  const round = (n, digits) => {
    const p = 10 ** digits;
    return Math.round(n * p + Math.sign(n) * 1e-9) / p;
  };
  const num = (n, digits) => round(n, digits).toLocaleString('ko-KR', { maximumFractionDigits: digits });

  FirstLabCalc.register({
    id: 'cbm',
    title: 'CBM 계산기',
    intro: '상자 크기와 수량을 넣으면 전체 CBM(㎥)과 세제곱피트, 우체국 EMS 부피중량을 계산해요.',
    inputs: [
      { name: 'unit', label: '길이 단위', type: 'select', default: 'cm', options: UNITS },
      { name: 'length', label: '가로', type: 'number', min: 0, step: 'any' },
      { name: 'width', label: '세로', type: 'number', min: 0, step: 'any' },
      { name: 'height', label: '높이', type: 'number', min: 0, step: 'any' },
      { name: 'qty', label: '상자 수량 (개)', type: 'number', min: 1, step: 1, default: 1 },
      { name: 'weight', label: '상자 1개 실제 무게 (kg, 선택)', type: 'number', min: 0, step: 'any', help: '넣으면 EMS 요금에 쓰이는 중량(실제 무게와 부피중량 중 큰 값)도 보여 줘요.' },
    ],

    compute(v, d, u) {
      const factor = d.cm_per_unit[v.unit];
      if (factor === undefined) throw new u.InputError('길이 단위를 골라 주세요.');
      const sides = [v.length, v.width, v.height].map(Number);
      if (sides.some((s) => !(s > 0))) throw new u.InputError('가로·세로·높이를 모두 넣으면 결과가 나와요.');
      const qty = Number(v.qty === undefined || v.qty === '' ? 1 : v.qty);
      if (!Number.isInteger(qty) || qty < 1) throw new u.InputError('수량은 1개 이상 정수로 넣어 주세요.');
      const hasWeight = v.weight !== undefined && v.weight !== null && v.weight !== '';
      const weight = hasWeight ? Number(v.weight) : null;
      if (hasWeight && !(weight >= 0)) throw new u.InputError('무게는 0 이상으로 넣어 주세요.');

      const cm = sides.map((s) => s * factor);
      const cm3Each = cm[0] * cm[1] * cm[2];
      const cbmEach = cm3Each / d.cm3_per_m3;
      const cbmTotal = cbmEach * qty;
      const ft3Total = cbmTotal / d.m3_per_ft3;
      const volKgEach = cm3Each / d.ems_divisor;

      const values = {
        cbm_each: round(cbmEach, 4),
        cbm_total: round(cbmTotal, 3),
        ft3_total: round(ft3Total, 2),
        ems_vol_kg_each: round(volKgEach, 2),
      };
      const rows = [
        ['상자 1개 크기 (cm)', cm.map((c) => num(c, 2)).join(' × ')],
        ['상자 1개 CBM', `${num(cbmEach, 4)} CBM`],
        [`전체 CBM (${qty.toLocaleString('ko-KR')}개)`, `${num(cbmTotal, 3)} CBM`],
        ['전체 세제곱피트', `${num(ft3Total, 2)} ft³`],
        ['EMS 부피중량 (1개, cm 곱 ÷ 6,000)', `${num(volKgEach, 2)}kg`],
      ];
      if (hasWeight) {
        const applied = Math.max(weight, volKgEach);
        values.ems_applied_kg_each = round(applied, 2);
        values.ems_basis = volKgEach > weight ? 'volume' : 'actual';
        rows.push(['EMS 적용 중량 (1개)', `${num(applied, 2)}kg (${volKgEach > weight ? '부피중량' : '실제 무게'} 기준)`]);
      }
      const notes = [
        '1CBM은 가로·세로·높이 1m인 상자 1개 부피(1㎥)예요. cm로 재면 세 변을 곱한 뒤 1,000,000으로 나눠요.',
        '해상·항공 화물 운임은 포워더·운송사마다 기준(무게 환산, 최소 운임, 끝수 처리)이 달라요. 견적서의 기준을 함께 보세요.',
        'EMS는 국가별 최대 규격이 달라 부피중량이 커도 접수가 제한될 수 있어요.',
      ];
      return { values, rows, notes };
    },
  });
})();
