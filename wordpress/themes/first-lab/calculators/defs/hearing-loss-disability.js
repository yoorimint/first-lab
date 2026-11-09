/* 소음성 난청 산재 장해급여 계산기: 두 귀 평균 청력손실치 → 장해등급(산재보험법 시행규칙 별표 5 제2호가목2), 명료도 조건 제외)
   → 평균임금 × 등급별 일수(산재보험법 별표 2). 평균임금은 최고·최저 보상기준 금액 안으로(법 제36조⑦). 금액은 data/hearing-loss-disability.json */
(function () {
  'use strict';

  /** 두 귀 dB → 등급 (명료도 조건 없이 dB 만으로 정해지는 기준, 높은 등급부터) */
  function gradeFor(a, b) {
    const hi = Math.max(a, b);
    const lo = Math.min(a, b);
    if (lo >= 90) return 4;               // 가) 두 귀 각각 90 이상
    if (lo >= 80) return 6;               // 나) 두 귀 각각 80 이상
    if (hi >= 90 && lo >= 70) return 6;   // 다) 한 귀 90 이상 + 다른 귀 70 이상
    if (lo >= 70) return 7;               // 라) 두 귀 각각 70 이상
    if (hi >= 90 && lo >= 60) return 7;   // 마) 한 귀 90 이상 + 다른 귀 60 이상
    if (lo >= 60) return 9;               // 바) 두 귀 각각 60 이상
    if (hi >= 80 && lo >= 50) return 9;   // 사) 한 귀 80 이상 + 다른 귀 50 이상
    if (hi >= 90) return 9;               // 아) 한 귀 90 이상
    if (hi >= 80) return 10;              // 자) 한 귀 80 이상 90 미만
    if (lo >= 50) return 10;              // 차) 두 귀 각각 50 이상
    if (hi >= 70) return 11;              // 카) 한 귀 70 이상 80 미만
    if (lo >= 40) return 11;              // 타) 두 귀 각각 40 이상
    if (hi >= 40) return 14;              // 파) 한 귀 40 이상 70 미만
    return 0;
  }

  FirstLabCalc.register({
    id: 'hearing-loss-disability',
    title: '소음성 난청 장해급여 계산기',
    intro: '두 귀의 평균 청력손실치(dB)와 1일 평균임금을 넣으면 산재 장해등급과 장해급여를 계산해요.',
    inputs: [
      { name: 'left_db', label: '왼쪽 귀 평균 청력손실치 (dB)', type: 'number', min: 0, max: 100, step: 1, default: 45, help: '청력검사 결과의 6분법 평균값이에요. 소수점 아래는 버려요.' },
      { name: 'right_db', label: '오른쪽 귀 평균 청력손실치 (dB)', type: 'number', min: 0, max: 100, step: 1, default: 45 },
      { name: 'avg_wage', label: '1일 평균임금 (원)', type: 'number', min: 0, step: 1, default: 100000, help: '진단 전 3개월 임금 합계를 그 기간 날수로 나눈 금액이에요. 퇴직 뒤 진단받았다면 공단이 정한 평균임금을 넣어요.' },
    ],

    compute(v, d, u) {
      // 빈 칸은 0dB 로 계산하지 않음 (11/9 검수: 한쪽 귀를 비우면 안내 없이 0dB 로 등급이 나왔음)
      if ([v.left_db, v.right_db].some((x) => String(x ?? '').trim() === '')) throw new u.InputError('두 귀의 평균 청력손실치를 모두 넣어 주세요.');
      const raw = [Number(v.left_db), Number(v.right_db)];
      if (raw.some((x) => !(x >= 0))) throw new u.InputError('두 귀의 평균 청력손실치를 0 이상으로 넣어 주세요.');
      const wage = Number(v.avg_wage);
      if (!(wage > 0)) throw new u.InputError('1일 평균임금을 넣어 주세요.');
      const floor = FirstLabCalc.util.floor;
      const [l, r] = raw.map((x) => Math.min(100, floor(x))); // 소수점 버림, 100 이상은 100 (시행규칙 별표 5)

      const grade = gradeFor(l, r);
      const base = Math.min(Math.max(floor(wage), d.min_comp), d.max_comp);
      const rows = [
        ['평균 청력손실치 (왼쪽 / 오른쪽)', `${l}dB / ${r}dB`],
      ];
      const notes = [];
      if (!grade) {
        rows.push(['장해등급', '해당 없음']);
        notes.push('두 귀 모두 40dB 미만이면 청력 장해등급에 해당하지 않아요. 소음성 난청 산재 인정 기준도 한 귀 40dB 이상이에요.');
        notes.push('말소리 알아듣는 정도(명료도)가 낮으면 더 높은 등급이 나올 수 있어 이 계산기는 dB 기준만 봤어요.');
        return { values: { grade: 0, base, lump: 0, pension: 0 }, rows, notes };
      }
      const g = d.grades[String(grade)];
      const lump = floor(base * g.lump_days);
      const pension = g.pension_days ? floor(base * g.pension_days) : 0;
      rows.push(['장해등급', `제${grade}급`]);
      rows.push([`계산에 쓰는 평균임금 (${u.won(d.min_comp)}~${u.won(d.max_comp)})`, u.won(base)]);
      rows.push([`장해보상일시금 (${g.lump_days}일분)`, u.won(lump)]);
      if (pension) rows.push([`장해보상연금 1년 치 (${g.pension_days}일분)`, u.won(pension)]);

      notes.push(`${d.year}년 최고 보상기준 금액 하루 ${u.won(d.max_comp)}, 최저 보상기준 금액 하루 ${u.won(d.min_comp)}을 넣었어요. 원 미만은 버렸어요.`);
      if (base !== floor(wage)) notes.push('평균임금이 보상기준 금액 범위 밖이라 그 범위의 끝 금액으로 계산했어요.');
      if (pension) notes.push('제4~7급은 연금과 일시금 중 하나를 골라요. 연금은 1년 치를 12번에 나눠 매달 25일에 받아요.');
      notes.push('말소리 알아듣는 정도(명료도)가 낮으면 더 높은 등급이 나올 수 있어 이 계산기는 dB 기준만 봤어요.');
      notes.push('난청과 함께 이명이 항상 있고 객관적 검사로 증명되면 제12급으로 정해질 수 있어요. 실제 등급과 금액은 근로복지공단이 정해요.');
      return { values: { grade, base, lump, pension }, rows, notes };
    },
  });
})();
