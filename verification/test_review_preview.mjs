import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import ts from 'typescript';
const api = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/lib/review-preview.ts', 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText, {exports: api});
const plain = value => JSON.parse(JSON.stringify(value));
const unit = {id: 'u1', text: '김😀민 / 김😀민 / 보존'};
const candidate = (id, start, patch = {}) => ({id, type: 'name', value: '김😀민', unitId: 'u1',
  start, end: start + 3, method: 'full', mask: [], confirmed: false, locationResolved: true, ...patch});
const saved = [candidate('first', 0), candidate('second', 6, {method: 'keep', confirmed: true}),
  candidate('unresolved', 12, {unitId: null, locationResolved: false})];
const before = JSON.stringify(saved);
const resultText = (candidates, mode) => api.previewSegments(unit, candidates).map(segment =>
  mode === 'detected' || !segment.masked ? segment.text : segment.deleted ? '' : '*'.repeat(segment.end - segment.start)).join('');

assert.equal(resultText(saved, 'detected'), unit.text, 'Detection preserves every original character, including emoji');
const detected = api.previewSegments(unit, saved).filter(s => s.candidates.length);
assert.deepEqual(plain(detected.map(s => s.candidates.map(c => c.id))), [['first'], ['second']], 'Kept candidates remain detected; unresolved ranges are not highlighted');

const draft = api.applyPreviewDecisions(saved, [{id: 'first', method: 'partial', mask: [[1, 2]]}]);
assert.equal(resultText(draft, 'masked'), '김*민 / 김😀민 / 보존');
assert.equal(draft[0].confirmed, false, 'A live preview never confirms a decision');
assert.equal(draft[1].method, 'keep', 'A scope-limited preview preserves other explicit decisions');
assert.equal(JSON.stringify(saved), before, 'Original export plan is untouched');
assert.equal(resultText(api.applyPreviewDecisions(saved, []), 'masked'), '*** / 김😀민 / 보존', 'Clearing the draft restores the saved plan');

const deleted = api.applyPreviewDecisions(saved, [{id: 'first', method: 'delete', mask: []}]);
assert.equal(resultText(deleted, 'masked'), ' / 김😀민 / 보존', 'Delete preview removes the value rather than showing a full mask');
const keep = api.applyPreviewDecisions(saved, [{id: 'first', method: 'keep', mask: []}]);
assert.equal(resultText(keep, 'masked'), unit.text);
const ignored = api.applyPreviewDecisions(saved, [{id: 'unresolved', method: 'delete', mask: []}, {id: 'missing', method: 'keep', mask: []}]);
assert.equal(JSON.stringify(ignored), before, 'Unresolved and stale IDs cannot change preview content');
assert.equal(api.hidesRange(draft[0], 0, 1), false);
assert.equal(api.hidesRange(draft[0], 1, 2), true, 'PDF character overlays use the same code-point mask');
assert.equal(api.hidesRange(draft[0], 2, 3), false);
assert.equal(api.hidesRange(draft[0], 3, 4), false);
assert.equal(api.hidesRange(deleted[0], 0, 3), true);
assert.equal(api.hidesRange(keep[0], 0, 3), false);
console.log('Review preview: original text, all detections, draft isolation, scope preservation, Unicode, partial/full/keep/delete and PDF ranges passed');
