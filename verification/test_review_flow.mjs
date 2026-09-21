import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import ts from 'typescript';
const api = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/lib/review-flow.ts', 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText, {exports: api});
const plain = value => JSON.parse(JSON.stringify(value));
const c = (id, type, value, extra = {}) => ({id, type, value, method: 'full', mask: [], confirmed: false, locationResolved: true, ...extra});
const items = [c('a', 'name', '가상인'), c('b', 'phone', '010-0000-1111'), c('c', 'name', '가상인'),
  c('d', 'name', '다른인', {confirmed: true, method: 'keep', decisionSource: 'user'})];
const categories = api.groupReviewCategories(items);
assert.equal(categories.length, 12);
assert.equal(new Set(categories.map(category => category.type)).size, 12);
assert.equal(categories.find(category => category.type === 'name').count, 3);
assert.equal(categories.find(category => category.type === 'name').groups.length, 2);
assert.equal(categories.find(category => category.type === 'passport').count, 0);
assert.equal(categories.reduce((n, category) => n + category.count, 0), items.length);
const basicOnly = api.reviewCategorySections(items);
assert.deepEqual(plain(basicOnly.primary.map(category=>category.type)), ['name','phone','email','address','dob','resident_id']);
assert.equal(basicOnly.additionalCount, 0);
assert.equal(basicOnly.additional.length, 6, 'Undetected extra types remain discoverable');
assert(basicOnly.additional.every(category=>category.count===0));
const withAdditional = [...items, c('passport1','passport','M12345678'), c('passport2','passport','M12345678'),
  c('account1','account','123-456', {method:'keep',confirmed:true,decisionSource:'user'})];
const snapshot = JSON.stringify(withAdditional);
const extra = api.reviewCategorySections(withAdditional);
assert.equal(extra.primary.length, 6);
assert.equal(extra.additionalCount, 3, 'Counts refer to locations, including repeated values');
assert.deepEqual(plain(extra.additional.map(category=>category.type)), ['foreign_id','passport','driver_license','account','card','management_id']);
assert.deepEqual(plain(extra.additional.filter(category=>category.count>0).map(category=>category.type)), ['passport','account']);
assert.equal(extra.additional.find(category=>category.type==='passport').groups.length, 1);
assert.equal(extra.additional.find(category=>category.type==='account').groups[0][0].method, 'keep');
assert.equal(JSON.stringify(withAdditional), snapshot, 'Grouping must preserve occurrence decisions');
const extraOnly = api.reviewCategorySections([c('foreign1','foreign_id','900101-5000000',{locationResolved:false})]);
assert.equal(extraOnly.primary.length, 6);
assert(extraOnly.primary.every(category=>category.count===0));
assert.equal(extraOnly.additionalCount, 1, 'Unresolved extra information must remain discoverable');
assert.equal(api.reviewCategorySections([]).additionalCount, 0);
assert.equal(api.isUserDecision({...items[0], confirmed: true, decisionSource: 'ai_automatic'}), false);
assert.equal(api.isUserDecision(items[3]), true);
const changes = api.changedPreviewDecisions(items, [{id:'a',method:'partial',mask:[[1,2]]},{id:'b',method:'full',mask:[]}]);
assert.deepEqual(plain(changes), [{id:'a',method:'partial',mask:[[1,2]],confirmed:true}]);
assert.equal(items[0].confirmed, false);
assert.equal(api.changedPreviewDecisions(items, [{id:'missing',method:'keep',mask:[]}]).length, 0);
const initialJSON=JSON.stringify(items);
const accepted=api.exportPlanDecisions(items,[{id:'a',method:'partial',mask:[[1,2]]}]);
assert.deepEqual(plain(accepted),[
  {id:'a',method:'partial',mask:[[1,2]],confirmed:true},
  {id:'b',method:'full',mask:[],confirmed:true},
  {id:'c',method:'full',mask:[],confirmed:true},
]);
assert.equal(JSON.stringify(items),initialJSON,'Building a request must not alter the saved plan');
assert(!accepted.some(d=>d.id==='d'),'An already saved keep must be preserved');
assert(accepted.every(d=>Object.keys(d).sort().join(',')==='confirmed,id,mask,method'),'Use only original plan fields');
assert.equal(api.exportPlanDecisions([{...items[0],confirmed:true}]).length,0,'Do not resubmit unchanged accepted settings');
assert.throws(()=>api.exportPlanDecisions([{...items[0],locationResolved:false}]));
assert.throws(()=>api.exportPlanDecisions(items,[{id:'missing',method:'keep',mask:[]}]));
assert.equal(api.exportPlanDecisions([]).length,0);
console.log('Six primary cards, six persistent extra types, detected counts, repeated locations and preserved decisions passed');
