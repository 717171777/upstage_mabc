/* Synthetic browser contract test. Every service request is mocked; external requests are blocked. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const BASE = process.env.GARIMI_KEYBOARD_TEST_URL || 'http://localhost:3000';

function syntheticJob() {
  const values = [
    ['name-a', 'name', '김가람'],
    ['email', 'email', 'mira@example.test'],
    ['name-b', 'name', '이하늘'],
    ['name-repeat', 'name', '김가람'],
    ['phone', 'phone', '010-1234-5678'],
  ];
  return {
    id: 'keyboard-synthetic', version: 1, status: 'review', fileName: 'keyboard-synthetic.docx',
    format: 'docx', aiEnabled: true, upstageRequired: true,
    expiresAt: new Date(Date.now() + 3600000).toISOString(),
    units: values.map(([id, , value]) => ({ id: `unit-${id}`, text: value })),
    candidates: values.map(([id, type, value]) => ({ id, type, value, unitId: `unit-${id}`, start: 0,
      end: Array.from(value).length, page: null, method: 'full', mask: [], confirmed: false, locationResolved: true })),
    metadata: [], uninspected: [], documentType: 'transaction_settlement', suggestions: [],
    analysis: { parse: {status:'completed'}, classify: {status:'completed'}, extract: {status:'completed'},
      hermes: {status:'completed', required:true}, warnings: [] },
    artifact: null, acknowledged: false, metadataReviewed: true,
    context: {recipient:null, purpose:'', keepInfo:null},
    runtime: {platform:'test', execution:'mock', model:'solar-pro4-260806', llmUsed:false},
  };
}

(async () => {
  const browser = await chromium.launch({headless:true});
  const context = await browser.newContext({viewport:{width:1440, height:1000}});
  const page = await context.newPage();
  let job = syntheticJob(), holdNextPlan = false, releasePlan, failNextPlan = false;
  const writes = [], errors = [], external = [], unexpected = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.addInitScript(() => sessionStorage.setItem('garimi-job', JSON.stringify({id:'keyboard-synthetic', token:'mock-only'})));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== BASE) { external.push(url.origin); return route.abort('blockedbyclient'); }
    return route.continue();
  });
  await context.route('**/api/service/**', async route => {
    const request = route.request(), url = new URL(request.url());
    let body;
    if (url.pathname.endsWith('/plan') && request.method() === 'PATCH') {
      const payload = request.postDataJSON();
      writes.push(structuredClone(payload));
      if (holdNextPlan) {
        holdNextPlan = false;
        await new Promise(resolve => { releasePlan = resolve; });
      }
      if (failNextPlan) {
        failNextPlan = false;
        return route.fulfill({status:500, contentType:'application/json', body:JSON.stringify({error:'검증용 저장 실패'})});
      }
      const updates = new Map(payload.candidates.map(candidate => [candidate.id, candidate]));
      job = {...job, version:job.version + 1, candidates:job.candidates.map(candidate => ({...candidate, ...updates.get(candidate.id)}))};
      body = job;
    } else if (url.pathname.endsWith('/preview')) {
      body = {format:'docx', units:job.units, metadata:[], uninspected:[], blocks:job.units.map(unit => ({kind:'paragraph', unitId:unit.id}))};
    } else if (url.pathname.endsWith('/jobs/keyboard-synthetic') && request.method() === 'GET') body = job;
    else {
      unexpected.push(`${request.method()} ${url.pathname}`);
      return route.fulfill({status:500, contentType:'application/json', body:'{"error":"Unexpected mock request"}'});
    }
    return route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(body)});
  });

  const editor = () => page.getByLabel('개인정보 편집', {exact:true});
  const openFirst = async () => {
    await page.getByRole('region', {name:/정보 목록$/}).getByRole('button').first().click();
    await editor().waitFor();
    await editor().locator('summary').filter({hasText:'추가 기능'}).click();
    await editor().getByRole('checkbox',{name:'키보드 단축키 사용',exact:true}).check();
  };
  const assertSelected = async value => assert.equal(await editor().locator('.text-base.font-medium').textContent(), value);
  const confirm = async () => {
    const response = page.waitForResponse(r => r.url().endsWith('/plan'));
    await editor().focus();
    await page.keyboard.press('Enter');
    await response;
    await page.waitForFunction(() => !document.querySelector('[aria-label="개인정보 편집"]')?.textContent.includes('저장 중…'));
  };
  try {
    await page.goto(BASE + '/decision', {waitUntil:'networkidle'});
    assert.equal(await editor().count(),0,'The initial screen shows only the list');

    // Six primary cards and six extra tags remain visible, including empty types.
    const categories = page.getByRole('group', {name:'개인정보 기본 6종', exact:true});
    assert.equal(await categories.getByRole('button').count(),6);
    const extraTypes=page.getByRole('group',{name:'개인정보 추가 6종',exact:true});
    assert.equal(await extraTypes.getByRole('button').count(),6);
    await extraTypes.getByRole('button',{name:'여권번호 0곳',exact:true}).click();
    await page.getByText('이번 문서에서 탐지된 항목이 없습니다.',{exact:true}).waitFor();
    await categories.getByRole('button',{name:'주민등록번호 0곳',exact:true}).click();
    await page.getByText('이번 문서에서 탐지된 항목이 없습니다.',{exact:true}).waitFor();
    await categories.getByRole('button',{name:'이름 3곳',exact:true}).click();
    assert.equal(await editor().count(),0,'Category selection must not open an editor');
    await openFirst();
    assert.equal(await categories.count(),0,'Settings replace the list rather than appear below it');
    await assertSelected('김가람');
    await editor().focus();

    // Choosing an option previews only; Tab saves that edit and moves without Enter.
    await page.keyboard.press('0');
    assert.equal(writes.length,0);
    holdNextPlan=true;
    const request=page.waitForRequest(r=>r.url().endsWith('/plan'));
    const response=page.waitForResponse(r=>r.url().endsWith('/plan'));
    await page.keyboard.press('Tab'); await request;
    for(const key of ['1','Enter','ArrowDown','Tab']) await page.keyboard.press(key);
    assert.equal(writes.length,1);
    assert.deepEqual(writes[0].candidates.map(c=>c.id),['name-a','name-repeat']);
    assert(writes[0].candidates.every(c=>c.method==='keep'&&c.confirmed));
    releasePlan();await response;
    await page.waitForFunction(()=>document.querySelector('[aria-label="같은 정보 2번째 위치"]')?.getAttribute('aria-pressed')==='true');
    await assertSelected('김가람');
    assert.equal(job.candidates.find(c=>c.id==='name-repeat').method,'keep');
    await editor().focus();await page.keyboard.press('Shift+Tab');
    assert.equal(writes.length,1,'Unchanged navigation must not create a review decision');
    await editor().focus();await page.keyboard.press('ArrowDown');
    await assertSelected('이하늘');
    assert.equal(writes.length,1);
    await page.keyboard.press('Enter');
    assert.equal(writes.length,1,'Enter on an unchanged AI default is optional and does not save');

    // Failed navigation keeps the current item and draft; retrying saves it.
    await page.keyboard.press('0');failNextPlan=true;
    const failed=page.waitForResponse(r=>r.url().endsWith('/plan'));
    await page.keyboard.press('Tab');await failed;
    await page.getByText('검증용 저장 실패',{exact:true}).waitFor();
    await assertSelected('이하늘');
    await editor().focus();await page.keyboard.press('Tab');
    await assertSelected('010-1234-5678');
    assert.equal(job.candidates.find(c=>c.id==='name-b').method,'keep');

    // Category navigation works across types; explicit Enter remains available.
    await page.getByRole('button',{name:'← 전화번호 목록으로',exact:true}).click();
    await categories.getByRole('button',{name:'이메일 1곳',exact:true}).click();
    await openFirst();
    await editor().focus();await page.keyboard.press('2');await confirm();
    await assertSelected('mira@example.test');
    assert.equal(job.candidates.find(c=>c.id==='email').method,'partial');

    // Composition, native controls, document selections, and dialogs retain native keys.
    const guards = await editor().evaluate(root => {
      const dispatch = (target, init = {}) => {
        const event = new KeyboardEvent('keydown', {key:'0', bubbles:true, cancelable:true, ...init});
        target.dispatchEvent(event);
        return event.defaultPrevented;
      };
      const composing = dispatch(root, {isComposing:true});
      const input = document.createElement('input'); root.appendChild(input);
      const native = ['0','Enter','Tab','ArrowDown','ArrowLeft','ArrowRight'].map(key => dispatch(input, {key}));
      input.remove();
      const range = document.createRange(); range.selectNodeContents(root.querySelector('.text-base.font-medium'));
      window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
      const selection = dispatch(root); window.getSelection().removeAllRanges();
      const modal = document.createElement('dialog'); modal.open = true; modal.textContent = 'Mock modal'; document.body.appendChild(modal);
      const dialog = dispatch(root); modal.remove();
      const repeated = dispatch(root, {repeat:true});
      return {composing, native, selection, dialog, repeated};
    });
    assert.deepEqual(guards, {composing:false, native:[false,false,false,false,false,false], selection:false, dialog:false, repeated:true});
    assert.equal(job.candidates.find(c => c.id === 'email').method, 'partial');
    await editor().focus();
    await page.keyboard.press('ArrowDown');
    await assertSelected('mira@example.test');
    await page.getByText('이 종류의 정보는 문서 안에 한 곳만 있습니다.', {exact:true}).waitFor();

    await page.keyboard.press('Escape');
    assert.equal(await editor().getByRole('checkbox', {name:'키보드 단축키 사용', exact:true}).isChecked(),false);
    const offCaptured = await editor().evaluate(root => {
      const event = new KeyboardEvent('keydown', {key:'0', bubbles:true, cancelable:true});
      root.dispatchEvent(event); return event.defaultPrevented;
    });
    assert.equal(offCaptured, false);
    // Verify semantic disclosure choices in the actual editor, using synthetic data only.
    const fs = require('node:fs');
    fs.mkdirSync('verification/semantic-presets', {recursive:true});
    const showType = async (type, value) => {
      job = syntheticJob();
      job.candidates = [{...job.candidates[0],type,value,end:Array.from(value).length}];
      job.units = [{...job.units[0],text:value}];
      await page.reload({waitUntil:'networkidle'});await openFirst();
      await editor().waitFor();
      await editor().focus();
    };
    await showType('address','경기도 수원시 영통구 영통동 123 101동 202호');
    await page.keyboard.press('3');
    await page.keyboard.press('ArrowLeft');
    assert.equal(await editor().getByRole('button',{name:/시·도까지만 공개/}).getAttribute('aria-pressed'),'true');
    await editor().getByRole('region',{name:'선택한 가림 결과'}).getByText('경기도'+'*'.repeat(26),{exact:true}).waitFor();
    await page.screenshot({path:'verification/semantic-presets/address-editor.png',fullPage:true});
    await confirm();
    await page.reload({waitUntil:'networkidle'});await openFirst();
    await editor().focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await editor().getByRole('button',{name:/시·도 \+ 시·군까지 공개/}).getAttribute('aria-pressed'),'true');
    await showType('dob','1995년 8월 17일');
    await page.keyboard.press('2');
    await editor().getByRole('region',{name:'선택한 가림 결과'}).getByText('1995년'+'*'.repeat(7),{exact:true}).waitFor();
    await page.screenshot({path:'verification/semantic-presets/dob-editor.png',fullPage:true});
    await confirm();
    assert.deepEqual(job.candidates[0].mask,[[5,12]]);

    // Contextual questions are optional; answers save only the exact occurrence.
    job=syntheticJob();
    job.candidates[0]={...job.candidates[0],type:'phone',value:'010-1234-5678',end:13};
    job.units[0].text='010-1234-5678';
    job.context={recipient:'외부협력기관',purpose:'업무 문의 전달',keepInfo:null};
    job.suggestions=[job.candidates[0],job.candidates[4]].map(c=>({candidateId:c.id,type:'question',
      recommendation:'full',content:'이 문의 연락처를 공유본에 남길까요?',question:'이 문의 연락처를 공유본에 남길까요?',
      evidence:'문의 연락처',reason:'답하기 전에는 전체 가림합니다.',source:'sp4'}));
    await page.reload({waitUntil:'networkidle'});await openFirst();
    await page.getByText('추가 질문 2개 · 선택 사항',{exact:true}).waitFor();
    const question=()=>editor().getByRole('region',{name:'AI 확인 질문'});
    await editor().locator('summary').filter({hasText:'AI의 추가 질문'}).click();
    await question().getByText('이 문의 연락처를 공유본에 남길까요?',{exact:true}).waitFor();
    await editor().locator('summary').filter({hasText:'적용 위치 바꾸기'}).click();
    await editor().getByRole('radio',{name:'이 위치만',exact:true}).check();
    fs.mkdirSync('verification/context-questions',{recursive:true});
    await page.screenshot({path:'verification/context-questions/questions.png',fullPage:true});
    const beforeAnswer=writes.length;
    holdNextPlan=true;
    const answerRequest=page.waitForRequest(r=>r.url().endsWith('/plan'));
    const answerResponse=page.waitForResponse(r=>r.url().endsWith('/plan'));
    await question().getByRole('button',{name:'네, 그대로 두기',exact:true}).click();
    await answerRequest;
    assert.equal(writes.length,beforeAnswer+1);
    assert.deepEqual(writes.at(-1).candidates,[{id:'name-a',method:'keep',mask:[],confirmed:true}]);
    assert(await question().getByRole('button',{name:'네, 그대로 두기',exact:true}).isDisabled());
    releasePlan();await answerResponse;
    await question().getByText(/직접 설정/).waitFor();
    assert.equal(job.candidates[4].method,'full');assert.equal(job.candidates[4].confirmed,false);
    await page.getByText('추가 질문 1개 · 선택 사항',{exact:true}).waitFor();
    await page.getByRole('button',{name:'질문 확인하기',exact:true}).click();
    await editor().locator('summary').filter({hasText:'AI의 추가 질문'}).click();
    failNextPlan=true;
    const failedAnswer=page.waitForResponse(r=>r.url().endsWith('/plan'));
    await question().getByRole('button',{name:'아니요, 모두 가리기',exact:true}).click();
    await failedAnswer;await editor().getByText('검증용 저장 실패',{exact:true}).waitFor();
    assert.equal(job.candidates[4].confirmed,false);
    await page.getByText('추가 질문 1개 · 선택 사항',{exact:true}).waitFor();
    const successAnswer=page.waitForResponse(r=>r.url().endsWith('/plan'));
    await question().getByRole('button',{name:'아니요, 모두 가리기',exact:true}).click();
    await successAnswer;await question().getByText(/직접 설정/).waitFor();
    assert.equal(job.candidates[4].confirmed,true);assert.equal(job.candidates[4].method,'full');
    assert.equal(await page.getByRole('region',{name:'확인할 AI 질문'}).count(),0);

    // A previous manual keep never replaces the displayed default at another position.
    job=syntheticJob();
    job.candidates[3]={...job.candidates[3],method:'keep',confirmed:true,decisionSource:'user'};
    await page.reload({waitUntil:'networkidle'});await openFirst();
    assert.equal(await editor().getByRole('button',{name:/^모두 가리기/}).getAttribute('aria-pressed'),'true');
    await editor().focus();await page.keyboard.press('2');await confirm();
    assert.equal(job.candidates[0].method,'partial');assert.equal(job.candidates[3].method,'keep');
    // Only untouched matching locations follow the edited choice.
    job=syntheticJob();await page.reload({waitUntil:'networkidle'});await openFirst();
    await editor().focus();await page.keyboard.press('2');await confirm();
    assert.deepEqual(job.candidates[0].mask,job.candidates[3].mask);
    assert(job.candidates[0].confirmed&&job.candidates[3].confirmed);
    assert.equal(await page.locator('[data-review-needed="true"]').count(),0);
    assert(await page.getByRole('button',{name:'이 설정으로 다음 →',exact:true}).isEnabled());

    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    assert.deepEqual(external, []);
    console.log(JSON.stringify({passed:true, checks:['list-to-settings drilldown','six primary types and conditional extras','draft preview without immediate save','Tab saves changed draft without Enter','unchanged AI defaults remain optional','same-type navigation','failed navigation preserves draft','manual repeated exception preserved','partial mask propagated','address hierarchy','DOB year only','optional question answers and retry','pending save lock','native/selection/modal/IME/repeat guards','Escape exits keyboard mode'], mockPlanRequests:writes.length, externalRequests:external.length, browserErrors:errors.length}));
  } finally {
    releasePlan?.();
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
