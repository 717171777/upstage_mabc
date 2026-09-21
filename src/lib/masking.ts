import type { PiiType, Method } from './service';

export type Mask = [number, number][];

export interface Grapheme {
  text: string;
  start: number;
  end: number;
}

export interface Preset {
  id: string;
  label: string;
  mask: Mask;
  result: string;
}

function codepointIndex(value: string, utf16Index: number): number {
  return Array.from(value.slice(0, utf16Index)).length;
}

function segmentToGrapheme(value: string, segment: Intl.SegmentData): Grapheme {
  const start = codepointIndex(value, segment.index);
  const text = segment.segment;
  const end = start + Array.from(text).length;
  return { text, start, end };
}

export function graphemes(value: string): Grapheme[] {
  const segmenter = new Intl.Segmenter('ko', { granularity: 'grapheme' });
  const segments = segmenter.segment(value);
  return Array.from(segments, (s) => segmentToGrapheme(value, s));
}

export function normalizeMask(mask: Mask, length: number): Mask {
  if (!Number.isInteger(length) || length < 0) {
    throw new Error('길이는 유효한 정수여야 합니다');
  }

  for (const pair of mask) {
    if (!Array.isArray(pair) || pair.length !== 2) {
      throw new Error('마스크 범위는 [시작, 끝] 쌍이어야 합니다');
    }
    const [start, end] = pair;
    if (!Number.isInteger(start) || !Number.isInteger(end)) {
      throw new Error('마스크 범위는 정수여야 합니다');
    }
    if (start < 0 || end > length || start >= end) {
      throw new Error('마스크 범위가 잘못되었습니다');
    }
  }

  const cloned: [number, number][] = mask.map(([s, e]) => [s, e]);
  const sorted = [...cloned].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: Mask = [];
  for (const range of sorted) {
    if (merged.length === 0) {
      merged.push(range);
      continue;
    }
    const last = merged[merged.length - 1];
    if (range[0] <= last[1]) {
      last[1] = Math.max(last[1], range[1]);
    } else {
      merged.push(range);
    }
  }
  return merged;
}

function graphemeBoundaries(value: string): Set<number> {
  const g = graphemes(value);
  const bounds = new Set<number>();
  for (const gr of g) {
    bounds.add(gr.start);
    bounds.add(gr.end);
  }
  return bounds;
}

export function validPartial(value: string, mask: Mask): boolean {
  const cpCount = Array.from(value).length;
  if (mask.length === 0) return false;

  let normalized: Mask;
  try {
    normalized = normalizeMask(mask, cpCount);
  } catch {
    return false;
  }

  if (normalized.length === 1 && normalized[0][0] === 0 && normalized[0][1] === cpCount) {
    return false;
  }
  const bounds = graphemeBoundaries(value);
  for (const [start, end] of normalized) {
    if (!bounds.has(start) || !bounds.has(end)) return false;
  }
  return true;
}

export function maskedText(value: string, method: Method, mask: Mask = []): string {
  if (method === 'keep') return value;
  if (method === 'delete') return '';
  if (method === 'full') return '*'.repeat(Array.from(value).length);

  const codepoints = Array.from(value);
  let normalized: Mask;
  try {
    normalized = normalizeMask(mask, codepoints.length);
  } catch {
    return value;
  }
  if (normalized.length === 0) return value;

  const parts: string[] = [];
  let ci = 0;
  for (const [start, end] of normalized) {
    while (ci < start) {
      parts.push(codepoints[ci]);
      ci++;
    }
    parts.push('*'.repeat(end - start));
    ci = end;
  }
  while (ci < codepoints.length) {
    parts.push(codepoints[ci]);
    ci++;
  }
  return parts.join('');
}

function graphemeRangeFromIndex(value: string, gi: number): [number, number] {
  const g = graphemes(value);
  if (gi < 0 || gi >= g.length) {
    throw new Error('Grapheme index out of range');
  }
  return [g[gi].start, g[gi].end];
}

export function maskFromIndices(value: string, selected: number[]): Mask {
  for (const idx of selected) {
    if (!Number.isInteger(idx)) {
      throw new Error('선택 인덱스는 정수여야 합니다');
    }
  }
  const unique = [...new Set(selected)].sort((a, b) => a - b);
  const ranges: [number, number][] = [];
  for (const gi of unique) {
    ranges.push(graphemeRangeFromIndex(value, gi));
  }
  return normalizeMask(ranges, Array.from(value).length);
}

// Keep only the comparison suffix, including masking separators in the prefix.
function suffixMask(value: string, count: number): Mask {
  if (!/^[A-Za-z0-9 ._+()/-]+$/.test(value)) return [];
  const indices = Array.from(value.matchAll(/[A-Za-z0-9]/g), m => m.index!);
  if (indices.length <= count) return [];
  return [[0, indices[indices.length - count]]];
}

function emailLocalPartEnd(value: string): number | null {
  // Match the server's conservative recommendation policy. Complex graphemes
  // remain available through direct selection, never a guessed preset offset.
  if (Array.from(value).length > 1000) return null;
  if (!/^[^@\s]+@[^@\s]+$/u.test(value)) return null;
  if (/[\p{M}\p{C}\u1100-\u11ff\ua960-\ua97f\ud7b0-\ud7ff\u0d4e\u{111c2}-\u{111c3}\u{1193f}\u{11941}\u{11a3a}\u{11a84}-\u{11a89}\u{11d46}\u{11f02}\u{1f1e6}-\u{1f1ff}\u{1f3fb}-\u{1f3ff}]/u.test(value)) return null;
  return codepointIndex(value, value.indexOf('@'));
}

const REGIONS = new Set(('서울 서울시 서울특별시 부산 부산시 부산광역시 대구 대구시 대구광역시 인천 인천시 인천광역시 광주 광주광역시 대전 대전시 대전광역시 울산 울산시 울산광역시 세종 세종시 세종특별자치시 경기 경기도 강원 강원도 강원특별자치도 충북 충청북도 충남 충청남도 전북 전라북도 전북특별자치도 전남 전라남도 경북 경상북도 경남 경상남도 제주 제주도 제주특별자치도').split(' '));

function addressPresets(value: string): { id: string; label: string; mask: Mask }[] {
  const tokens = Array.from(value.matchAll(/\S+/gu));
  if (!tokens.length || tokens[0].index !== 0 || !REGIONS.has(tokens[0][0])) return [];
  const result: { id: string; label: string; mask: Mask }[] = [];
  const add = (index: number, id: string, label: string) => {
    if (index + 1 >= tokens.length) return;
    const end = codepointIndex(value, tokens[index].index! + tokens[index][0].length);
    result.push({ id, label, mask: [[end, Array.from(value).length]] });
  };
  add(0, 'address_region', '시·도까지만 공개');
  let i = 1;
  if (tokens[i] && /^[가-힣]+[시군구]$/.test(tokens[i][0])) {
    add(i, 'address_city', /구$/.test(tokens[i][0]) ? '시·도 + 구까지 공개' : '시·도 + 시·군까지 공개');
    i++;
    if (tokens[i] && /[시군]$/.test(tokens[i-1][0]) && /^[가-힣]+구$/.test(tokens[i][0])) {
      add(i, 'address_district', '시·군 + 구까지 공개');
      i++;
    }
  }
  if (tokens[i] && /^[가-힣][가-힣0-9·]*[읍면동]$/.test(tokens[i][0])) {
    add(i, 'address_locality', '읍·면·동까지 공개');
  }
  return result;
}

function dobYearMask(value: string): Mask {
  const match = value.match(/^([0-9]{4})([-./])([0-9]{1,2})\2([0-9]{1,2})$/)
    || value.match(/^([0-9]{4})(년\s*)([0-9]{1,2})월\s*([0-9]{1,2})일$/)
    || value.match(/^([0-9]{4})()([0-9]{2})([0-9]{2})$/);
  if (!match) return [];
  const year = Number(match[1]), month = Number(match[3]), day = Number(match[4]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]) return [];
  return [[value[4] === '년' ? 5 : 4, Array.from(value).length]];
}


function digitPositions(value: string): number[] {
  const cps = Array.from(value);
  const out: number[] = [];
  cps.forEach((c, i) => { if (c >= '0' && c <= '9') out.push(i); });
  return out;
}

// 숫자 위치를 연속 구간으로 합친다. 구분자는 가리지 않는다.
function mergePositions(positions: number[]): Mask {
  const ranges: [number, number][] = [];
  for (const p of positions) {
    const last = ranges[ranges.length - 1];
    if (last && last[1] === p) last[1] = p + 1;
    else ranges.push([p, p + 1]);
  }
  return ranges;
}

function digitGroups(value: string): [number, number][] {
  const cps = Array.from(value);
  const groups: [number, number][] = [];
  let start = -1;
  cps.forEach((c, i) => {
    const digit = c >= '0' && c <= '9';
    if (digit && start < 0) start = i;
    if (!digit && start >= 0) { groups.push([start, i]); start = -1; }
  });
  if (start >= 0) groups.push([start, cps.length]);
  return groups;
}

function cardIsmsMask(value: string): Mask {
  if (!/^[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}$/.test(value)) return [];
  return mergePositions(digitPositions(value).slice(6, 12));
}

interface ResidentMasks { birthGender: Mask; birthOnly: Mask; genderOnly: Mask; yearOnly: Mask }

// 뒷 7자리는 그 자체로 개인을 특정하므로 노출하는 프리셋을 두지 않는다.
function residentMasks(value: string): ResidentMasks | null {
  const m = value.match(/^([0-9]{6})([- ]?)([0-9])([0-9]{6})$/);
  if (!m) return null;
  const cpCount = Array.from(value).length;
  const positions = digitPositions(value);
  const gender = positions[6];
  return {
    birthGender: [[gender + 1, cpCount]],
    birthOnly: [[gender, cpCount]],
    genderOnly: [...mergePositions(positions.slice(0, 6)), [gender + 1, cpCount]],
    yearOnly: [...mergePositions(positions.slice(2, 6)), [gender, cpCount]],
  };
}

export function partialDisclosureHint(type: PiiType): string {
  switch (type) {
    case 'dob': return '연도만 남기면 출생 연도를 알 수 있고, 월·일은 보이지 않습니다.';
    case 'address': return '시·도 → 시·군·구 → 읍·면·동 순으로 더 좁은 지역이 드러납니다. 도로명·건물·호수는 가립니다.';
    case 'phone': return '개인 연락처는 전체 가림이 기본입니다. 번호 대조가 필요할 때만 끝자리를 남기세요.';
    case 'email': return '도메인은 소속이나 이메일 제공자를 드러낼 수 있습니다. 계정 식별이 필요할 때만 첫 글자를 남기세요.';
    case 'account': case 'card': case 'management_id': return '전체 가림이 기본입니다. 업무상 번호 대조가 필요할 때만 끝 2자리 또는 4자리를 남기세요.';
    case 'resident_id': case 'foreign_id': case 'passport': case 'driver_license': return '신분증 번호는 전체 가림을 권장합니다. 생년월일 등이 포함될 수 있어 자동 일부 가림은 제공하지 않습니다.';
    case 'name': return '이름을 구분해야 할 때만 첫 글자를 남기세요. 다른 정보와 함께 보면 개인을 유추할 수 있습니다.';
  }
}

export function presetsFor(type: PiiType, value: string): Preset[] {
  const cpCount = Array.from(value).length;
  if (cpCount === 0 || cpCount > 1000) return [];
  if (/[\p{M}\p{Cs}\u200d\ufe00-\ufe0f\u{e0100}-\u{e01ef}\u{1f1e6}-\u{1f1ff}]/u.test(value)) return [];

  const presets: Preset[] = [];

  const addPreset = (id: string, label: string, mask: Mask): void => {
    if (mask.length === 0) return;
    let normalized: Mask;
    try {
      normalized = normalizeMask(mask, cpCount);
    } catch {
      return;
    }
    if (!validPartial(value, normalized)) return;
    const result = maskedText(value, 'partial', normalized);
    if (result === value) return;
    presets.push({ id, label, mask: normalized, result });
  };

  switch (type) {
    case 'phone': {
      const digits = value.replace(/[^0-9]/g, '');
      if (/^\+?[0-9 ()-]+$/.test(value) && digits.length >= 9 && digits.length <= 15) {
        const groups = digitGroups(value);
        if (groups.length === 3) {
          addPreset('phone_middle', '국번만 가림 · 010-****-5678', [[groups[1][0], groups[1][1]]]);
          addPreset('phone_tail', '끝자리만 가림 · 010-1234-****', [[groups[2][0], groups[2][1]]]);
        }
        addPreset('phone_suffix4', '끝 4자리만 남김 · 번호 대조', suffixMask(value, 4));
        addPreset('phone_suffix2', '끝 2자리만 남김 · 번호 대조', suffixMask(value, 2));
      }
      break;
    }
    case 'email': {
      const localEnd = emailLocalPartEnd(value);
      if (localEnd !== null) {
        if (localEnd > 1) addPreset('email_first_and_domain', '첫 글자와 도메인 남김', [[1, localEnd]]);
        if (localEnd > 2) addPreset('email_keep2', '앞 2글자와 도메인 남김', [[2, localEnd]]);
        addPreset('email_local_part', '도메인만 남김', [[0, localEnd]]);
      }
      break;
    }
    case 'address':
      for (const p of addressPresets(value)) addPreset(p.id, p.label, p.mask);
      break;
    case 'account': case 'card': case 'management_id':
      if (/^[A-Za-z0-9 ._/-]+$/.test(value)) {
        if (type === 'card') addPreset('card_isms', '가운데만 가림 · 1234-56**-****-3456', cardIsmsMask(value));
        addPreset('id_suffix4', '끝 4자리만 남김 · 번호 대조', suffixMask(value, 4));
        addPreset('id_suffix2', '끝 2자리만 남김 · 번호 대조', suffixMask(value, 2));
      }
      break;
    case 'dob':
      addPreset('dob_year_only', '출생 연도만 공개', dobYearMask(value));
      break;
    case 'name':
      // Do not guess surnames, compound surnames, or foreign name structure.
      if (/^[가-힣]{2,4}$/.test(value)) {
        if (cpCount > 2) addPreset('name_middle', '가운데만 가림 · 홍*동', [[1, cpCount - 1]]);
        addPreset('name_initial', '이름 첫 글자만 남김', [[1, cpCount]]);
      }
      break;
    case 'resident_id': case 'foreign_id': {
      const r = residentMasks(value);
      if (r) {
        addPreset('rrn_year_only', '출생 연도만 남김 · 90****-*******', r.yearOnly);
        addPreset('rrn_gender_only', '성별 한 자리만 남김 · ******-1******', r.genderOnly);
        addPreset('rrn_birth_only', '생년월일만 남김 · 900101-*******', r.birthOnly);
        addPreset('rrn_birth_gender', '생년월일·성별 남김 · 900101-1****** (재식별 주의)', r.birthGender);
      }
      break;
    }
    // Identity documents and unknown formats have full masking + direct selection.
    default: break;
  }

  return presets.slice(0, 5);
}

export function selectedTextRange(
  container: HTMLElement,
  selection: Selection | null
): { start: number; end: number } | null {
  try {
    if (!selection) return null;
    if (selection.rangeCount === 0) return null;

    const range = selection.getRangeAt(0);
    if (!range) return null;
    if (range.collapsed) return null;

    const startContainer = range.startContainer;
    const endContainer = range.endContainer;

    if (startContainer !== container && !container.contains(startContainer)) return null;
    if (endContainer !== container && !container.contains(endContainer)) return null;

    const prefixRange = document.createRange();
    prefixRange.selectNodeContents(container);
    prefixRange.setEnd(range.startContainer, range.startOffset);

    const prefixUtf16 = prefixRange.toString().length;
    const endUtf16 = prefixUtf16 + range.toString().length;

    const fullText = container.textContent;
    if (fullText === null || fullText.length === 0) return null;
    if (endUtf16 > fullText.length || endUtf16 <= prefixUtf16) return null;

    const segmenter = new Intl.Segmenter('ko', { granularity: 'grapheme' });
    const boundaries = new Set<number>();
    for (const seg of segmenter.segment(fullText)) {
      boundaries.add(seg.index);
      boundaries.add(seg.index + seg.segment.length);
    }

    if (!boundaries.has(prefixUtf16) || !boundaries.has(endUtf16)) return null;

    const start = Array.from(fullText.slice(0, prefixUtf16)).length;
    const end = Array.from(fullText.slice(0, endUtf16)).length;

    if (start >= end) return null;
    return { start, end };
  } catch {
    return null;
  }
}
