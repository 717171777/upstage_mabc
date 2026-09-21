import type {Candidate, PiiType} from './service';
import type {PreviewDecision} from './review-preview';

export const PRIMARY_REVIEW_TYPES: PiiType[] = [
  'name', 'phone', 'email', 'address', 'dob', 'resident_id',
];
export const REVIEW_TYPES: PiiType[] = [
  ...PRIMARY_REVIEW_TYPES,
  'foreign_id', 'passport', 'driver_license', 'account', 'card', 'management_id',
];

export function groupReviewCategories(candidates: Candidate[]) {
  return REVIEW_TYPES.map(type => {
    const items = candidates.filter(candidate => candidate.type === type);
    const groups = new Map<string, Candidate[]>();
    for (const candidate of items) groups.set(candidate.value, [...(groups.get(candidate.value) ?? []), candidate]);
    return {type, count: items.length, groups: [...groups.values()]};
  });
}

export function reviewCategorySections(candidates: Candidate[]) {
  const categories = groupReviewCategories(candidates);
  const primary = categories.filter(category => PRIMARY_REVIEW_TYPES.includes(category.type));
  const additional = categories.filter(category => !PRIMARY_REVIEW_TYPES.includes(category.type));
  return {categories, primary, additional, additionalCount: additional.reduce((sum, category) => sum + category.count, 0)};
}

export function isUserDecision(candidate: Candidate) {
  return candidate.confirmed && !['ai_automatic', 'local_automatic', 'ai_default'].includes(candidate.decisionSource ?? 'user');
}

export function changedPreviewDecisions(candidates: Candidate[], preview: PreviewDecision[]) {
  return preview.filter(decision => {
    const saved = candidates.find(candidate => candidate.id === decision.id);
    return saved?.locationResolved && (saved.method !== decision.method || JSON.stringify(saved.mask) !== JSON.stringify(decision.mask));
  }).map(decision => ({...decision, confirmed: true as const}));
}

// The user accepts the displayed settings together when choosing Next.
// Use only the original plan contract; do not rerun or reinterpret AI suggestions.
export function exportPlanDecisions(candidates: Candidate[], preview: PreviewDecision[] = []) {
  if (candidates.some(candidate => !candidate.locationResolved)) throw new Error('원문 위치를 연결한 뒤 다음 단계로 이동해 주세요.');
  const drafts = new Map(preview.map(decision => [decision.id, decision]));
  if (preview.some(decision => !candidates.some(candidate => candidate.id === decision.id))) throw new Error('변경할 정보를 다시 선택해 주세요.');
  return candidates.flatMap(candidate => {
    const decision = drafts.get(candidate.id) ?? candidate;
    if (candidate.confirmed && candidate.method === decision.method && JSON.stringify(candidate.mask) === JSON.stringify(decision.mask)) return [];
    return [{id: candidate.id, method: decision.method, mask: decision.mask.map(([start,end])=>[start,end] as [number,number]), confirmed: true as const}];
  });
}
