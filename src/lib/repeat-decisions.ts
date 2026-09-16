import type { Candidate } from './service';

function sameInformation(a: Candidate, b: Candidate): boolean {
  return a.type === b.type && a.value === b.value;
}

function manuallyConfirmed(candidate: Candidate): boolean {
  return candidate.confirmed && candidate.decisionSource !== 'ai_automatic' && candidate.decisionSource !== 'local_automatic';
}

// The current location can be edited; other explicit choices remain untouched.
export function followingTargets(candidate: Candidate, all: Candidate[]): Candidate[] {
  return all.filter(c => c.id === candidate.id || (sameInformation(c, candidate) && !manuallyConfirmed(c)));
}

export function previousChoice(candidate: Candidate, all: Candidate[]): Candidate | undefined {
  if (manuallyConfirmed(candidate)) return undefined;
  const prior = all.filter(c => c.id !== candidate.id && sameInformation(c, candidate) && manuallyConfirmed(c));
  const choice = prior[0];
  // Conflicting explicit choices have no unambiguous default to inherit.
  return choice && prior.every(c => c.method === choice.method && JSON.stringify(c.mask) === JSON.stringify(choice.mask)) ? choice : undefined;
}

// Document order between values; every occurrence of each value stays reachable.
export function repeatedReviewOrder(candidates: Candidate[]): Candidate[] {
  const groups = new Map<string, Candidate[]>();
  for (const c of candidates) {
    const key = JSON.stringify([c.type, c.value]);
    const group = groups.get(key);
    if (group) group.push(c); else groups.set(key, [c]);
  }
  return [...groups.values()].flat();
}
