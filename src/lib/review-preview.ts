import type {Candidate, Unit} from './service';

export type PreviewDecision = Pick<Candidate, 'id' | 'method' | 'mask'>;
export type ReviewViewMode = 'detected' | 'masked';

// Preview changes never confirm a candidate or alter the saved export plan.
export function applyPreviewDecisions(candidates: Candidate[], decisions: PreviewDecision[]): Candidate[] {
  const byId = new Map(decisions.map(decision => [decision.id, decision]));
  return candidates.map(candidate => {
    const draft = byId.get(candidate.id);
    return draft && candidate.locationResolved
      ? {...candidate, method: draft.method, mask: draft.mask.map(([start, end]) => [start, end] as [number, number])}
      : candidate;
  });
}

export function hidesRange(candidate: Candidate, start: number, end: number): boolean {
  if (candidate.start === null || candidate.end === null || start < candidate.start || end > candidate.end) return false;
  return candidate.method === 'full' || candidate.method === 'delete'
    || (candidate.method === 'partial' && candidate.mask.some(([a, b]) => start >= candidate.start! + a && end <= candidate.start! + b));
}

export function previewSegments(unit: Unit, candidates: Candidate[]) {
  const chars = Array.from(unit.text);
  const located = candidates.filter(c => c.locationResolved && c.unitId === unit.id && c.start !== null && c.end !== null);
  const bounds = new Set([0, chars.length]);
  for (const candidate of located) {
    bounds.add(candidate.start!); bounds.add(candidate.end!);
    if (candidate.method === 'partial') for (const [start, end] of candidate.mask) {
      bounds.add(candidate.start! + start); bounds.add(candidate.start! + end);
    }
  }
  const sorted = [...bounds].filter(n => n >= 0 && n <= chars.length).sort((a, b) => a - b);
  return sorted.slice(0, -1).map((start, index) => {
    const end = sorted[index + 1];
    const covering = located.filter(c => c.start! <= start && c.end! >= end);
    const hidden = covering.filter(c => hidesRange(c, start, end));
    return {start, end, text: chars.slice(start, end).join(''), candidates: covering,
      masked: hidden.length > 0, deleted: hidden.length > 0 && hidden.every(c => c.method === 'delete')};
  });
}
