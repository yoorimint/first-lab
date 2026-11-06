/* 일조권 이격거리 계산기: 전용·일반주거지역 정북방향 인접 대지경계선 이격거리 (건축법 제61조제1항, 2026.11.12 개정).
 * 건물 각 부분의 높이 → 띄어야 하는 최소 거리, 또는 대지경계선에서 거리 → 그 자리에 올릴 수 있는 최대 높이.
 * 허가 신청(건축신고)일이 개정 시행일 전이면 종전 기준 (부칙 제4조). 구간 값은 data/sunlight-setback.json */
(function () {
  'use strict';

  const round = (n, digits) => {
    const p = 10 ** digits;
    return Math.round(n * p + Math.sign(n) * 1e-9) / p;
  };
  const m = (n) => `${round(n, 3).toLocaleString('ko-KR', { maximumFractionDigits: 3 })}m`;

  /** 높이 h(m) 부분이 띄어야 하는 최소 거리 */
  function distanceFor(h, rule) {
    if (h <= rule.low_height) return rule.low_distance;
    if (rule.mid_height && h <= rule.mid_height) return rule.mid_distance;
    return h * rule.high_ratio;
  }
  /** 경계선에서 d(m) 떨어진 자리의 최대 높이 (null = 지을 수 없음) */
  function heightFor(d, rule) {
    if (d < rule.low_distance) return null;
    const byRatio = d / rule.high_ratio;
    if (rule.mid_height) {
      if (d < rule.mid_distance) return rule.low_height;
      return Math.max(rule.mid_height, byRatio);
    }
    return Math.max(rule.low_height, byRatio);
  }

  FirstLabCalc.register({
    id: 'sunlight-setback',
    title: '일조권 이격거리 계산기',
    intro: '전용·일반주거지역에서 정북방향 인접 대지경계선으로부터 띄어야 하는 법정 최소 거리를 높이별로 계산해요. 개정 전 기준과 같이 보여 줘요.',
    inputs: [
      { name: 'mode', label: '무엇을 계산할까요', type: 'select', default: 'height', options: [['height', '건물 높이 → 띄울 거리'], ['distance', '경계선에서 거리 → 최대 높이']] },
      { name: 'value', label: '건물 부분의 높이 또는 경계선에서 거리 (m)', type: 'number', min: 0, step: 'any', default: 15, help: '높이는 그 부분의 높이(지표면 기준), 거리는 정북방향 인접 대지경계선까지의 수평거리예요.' },
      { name: 'apply', label: '건축허가 신청(또는 건축신고)일', type: 'date', default: 'today', help: '2026년 11월 12일 이후 신청분부터 새 기준이에요 (건축위원회 심의 신청 포함).' },
    ],

    compute(v, d, u) {
      const x = Number(v.value);
      if (v.value === '' || v.value === undefined || !(x > 0)) throw new u.InputError('높이 또는 거리를 0보다 크게 넣어 주세요.');
      const t = u.parseDate(v.apply);
      if (t === null) throw new u.InputError('신청일을 넣어 주세요.');
      const isNew = v.apply >= d.new_rule_from;
      const now = isNew ? d.new_rule : d.old_rule;
      const values = { rule: isNew ? 'new' : 'old' };
      const rows = [['적용 기준', isNew ? `개정 기준 (${d.new_rule_from} 이후 신청)` : `종전 기준 (${d.new_rule_from} 전 신청)`]];
      const notes = [];
      if (v.mode === 'distance') {
        const hNew = heightFor(x, d.new_rule);
        const hOld = heightFor(x, d.old_rule);
        const h = isNew ? hNew : hOld;
        values.max_height = h === null ? 0 : round(h, 3);
        rows.push([`경계선에서 ${m(x)} 자리의 최대 높이`, h === null ? `이 기준으로는 건축 불가 (${m(now.low_distance)} 이상 띄워야 함)` : m(h)]);
        rows.push(['개정 기준이면', hNew === null ? '이 기준으로는 건축 불가' : m(hNew)]);
        rows.push(['종전 기준이면', hOld === null ? '이 기준으로는 건축 불가' : m(hOld)]);
      } else {
        const dNew = distanceFor(x, d.new_rule);
        const dOld = distanceFor(x, d.old_rule);
        values.distance = round(isNew ? dNew : dOld, 3);
        rows.push([`높이 ${m(x)} 부분이 띄울 최소 거리`, m(values.distance)]);
        rows.push(['개정 기준이면', m(dNew)]);
        rows.push(['종전 기준이면', m(dOld)]);
        if (isNew && dOld > dNew) notes.push(`개정으로 이 높이에서는 ${m(dOld - dNew)} 덜 띄워도 돼요.`);
      }
      notes.push('실제 거리는 이 법정 범위 안에서 시·군·구 건축 조례가 정해요. 허가 전에 해당 지역 조례를 확인해 보세요.');
      notes.push('2층 이하·높이 8m 이하 건물은 조례에 따라 이 기준을 적용하지 않을 수 있어요.');
      notes.push('정북방향 인접 대지가 전용·일반주거지역이 아니거나, 사이에 도로·공원·하천 등이 있으면 적용이 빠지거나 기준선이 달라져요.');
      if (!isNew) notes.push('2026년 11월 12일 전에 허가를 신청했거나 건축신고를 했다면 종전 기준(10m 초과 부분은 높이의 2분의 1)이에요.');
      return { values, rows, notes };
    },
  });
})();
