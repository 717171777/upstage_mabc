const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('typescript');
const source=fs.readFileSync(process.argv[2]||'src/lib/masking.ts','utf8');
const api={};vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:api,Intl,Array,Set,Number,Error});
const plain=v=>JSON.parse(JSON.stringify(v));
assert.deepEqual(plain(api.graphemes('가😀나')).map(x=>[x.start,x.end]),[[0,2],[2,3],[3,4]]);
const family='👨‍👩‍👧‍👦';assert.equal(api.graphemes(family).length,1);
assert.equal(api.maskedText('김😀민','partial',[[1,2]]),'김*민');
assert.equal(api.maskedText('가나다','partial',[[0,2]]),'**나다');
assert.equal(api.validPartial('가나다',[[0,1]]),false);
assert.equal(api.validPartial('abc',[[0,3]]),false);
assert.equal(api.validPartial('abc',[[0,8]]),false);
assert.equal(api.maskedText('김서준','full'),'***');
assert.equal(api.maskedText('010-1234-5678','full'),'*'.repeat(13));
assert.equal(api.maskedText('ABC','delete'),'');
const raw=[[1,2],[2,4]], before=JSON.stringify(raw);assert.deepEqual(plain(api.normalizeMask(raw,5)),[[1,4]]);assert.equal(JSON.stringify(raw),before);
for(const [type,value,labelPart,expected] of [
 ['phone','010 1234 5678','끝 2자리','*'.repeat(11)+'78'],
 ['address','서울시 강남구 테헤란로 1','시·도까지만','서울시'+'*'.repeat(11)],
 ['account','123-456-7890','끝 4자리','*'.repeat(8)+'7890']]) {
 const p=api.presetsFor(type,value).find(x=>x.label.includes(labelPart));assert.ok(p,labelPart);assert.equal(p.result,expected);
}
for(const type of ['name','phone','address','account','card','dob']) for(const value of ['김','김😀민','1','1234','가나다',family+'가']) {
 for(const p of api.presetsFor(type,value))assert.equal(api.validPartial(value,p.mask),true);
}
const emailCases=JSON.parse(fs.readFileSync(require('node:path').join(__dirname,'email_preset_cases.json'),'utf8'));
for(const c of emailCases) {
 const presets=plain(api.presetsFor('email',c.value));
 const first=presets.find(p=>p.id==='email_first_and_domain');
 const local=presets.find(p=>p.id==='email_local_part');
 assert.deepEqual(first?.mask??null,c.firstMask,c.value);
 assert.deepEqual(local?.mask??null,c.localMask,c.value);
 if(first) {
  assert.equal(presets[0].id,'email_first_and_domain');
  assert.equal(first.result,c.firstResult);
  assert.equal(api.validPartial(c.value,first.mask),true);
 }
 if(local) {
  assert.equal(local.result,'*'.repeat(Array.from(c.value.slice(0,c.value.indexOf('@'))).length)+c.value.slice(c.value.indexOf('@')));
  assert.equal(api.validPartial(c.value,local.mask),true);
 }
}
console.log('Mask helpers: graphemes, codepoints, immutable ranges and actual presets passed');

const semanticCases=JSON.parse(fs.readFileSync(require('node:path').join(__dirname,'semantic_preset_cases.json'),'utf8'));
for(const c of semanticCases) {
 const presets=api.presetsFor(c.type,c.value);
 assert.deepEqual(plain(Object.fromEntries(presets.map(p=>[p.id,p.result]))),c.results,`${c.type}: ${c.value}`);
 for(const p of presets) assert.equal(api.validPartial(c.value,p.mask),true);
}
console.log(`Semantic disclosure contracts: ${semanticCases.length} passed`);
