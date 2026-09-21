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
  const assertSelected = async value => assert.equal(await editor().locator('.text-base.font-medium').textContent(), value);
  const confirm = async () => {
    const response = page.waitForResponse(r => r.url().endsWith('/plan'));
    await editor().focus();
    await page.keyboard.press('Enter');
    await response;
    await editor().getByRole('button', {name:'선택 확정 · Enter', exact:true}).waitFor();
  };
  try {
    await page.goto(BASE + '/decision', {waitUntil:'networkidle'});
    await editor().waitFor();
    await assertSelected('김가람');
    await editor().focus();

    // Choosing 0 only changes the draft. Movement must neither save nor discard it.
    await page.keyboard.press('0');
    assert.equal(await editor().getByRole('button', {name:'0 유지', exact:true}).getAttribute('aria-pressed'), 'true');
    assert.equal(writes.length, 0);
    for (const key of ['ArrowDown', 'Tab']) {
      await page.keyboard.press(key);
      await assertSelected('김가람');
      assert.equal(writes.length, 0);
    }
    await editor().getByText('먼저 Enter로 현재 선택을 확정해 주세요. 선택은 그대로 유지됩니다.', {exact:true}).waitFor();

    // A pending save ignores repeated shortcuts and preserves the exact chosen method.
    holdNextPlan = true;
    const request = page.waitForRequest(r => r.url().endsWith('/plan'));
    const response = page.waitForResponse(r => r.url().endsWith('/plan'));
    await page.keyboard.press('Enter');
    await request;
    for (const key of ['1', 'Enter', 'ArrowLeft', 'ArrowRight', 'ArrowDown', 'Tab']) await page.keyboard.press(key);
    assert.equal(writes.length, 1);
    assert.equal(writes[0].candidates[0].method, 'keep');
    assert.equal(writes[0].candidates[0].confirmed, true);
    releasePlan();
    await response;
    await editor().getByRole('button', {name:'선택 확정 · Enter', exact:true}).waitFor();
    await assertSelected('김가람');
    assert.equal(job.candidates[0].method, 'keep');
    await page.keyboard.press('Enter');
    assert.equal(writes.length, 1, 'Unchanged confirmed selections should not save again');

    // Tab reaches duplicate occurrences before moving to the next distinct value.
    assert.equal(job.candidates.find(c=>c.id==='name-repeat').method,'keep');
    assert.equal(job.candidates.find(c=>c.id==='name-repeat').confirmed,true);
    assert.deepEqual(writes[0].candidates.map(c=>c.id),['name-a','name-repeat']);
    await page.keyboard.press('Tab');
    await assertSelected('김가람');
    assert.equal(await page.getByRole('button',{name:'같은 정보 2번째 위치',exact:true}).getAttribute('aria-pressed'),'true');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.getByRole('button',{name:'같은 정보 1번째 위치',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(writes.length,1);
    // Down still walks all names in document order.
    await page.keyboard.press('ArrowDown');
    await assertSelected('이하늘');
    assert.equal(writes.length, 1, 'Moving must not save');
    await confirm();
    await page.keyboard.press('ArrowDown');
    await assertSelected('김가람');
    const beforeAlreadyConfirmed=writes.length;
    await page.keyboard.press('Enter');
    assert.equal(writes.length,beforeAlreadyConfirmed);
    await page.keyboard.press('Shift+ArrowDown');
    await assertSelected('이하늘');
    await page.keyboard.press('Shift+Tab');
    await assertSelected('mira@example.test');

    // Arrow keys choose numbered drafts, including restored selections, without saving.
    const beforeArrows = writes.length;
    await page.keyboard.press('3');
    await page.keyboard.press('ArrowLeft');
    assert.equal(await editor().getByRole('button', {name:/첫 글자와 도메인 남김/}).getAttribute('aria-pressed'), 'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await editor().getByRole('button', {name:/앞 2글자와 도메인 남김/}).getAttribute('aria-pressed'), 'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await editor().getByRole('button', {name:/도메인만 남김/}).getAttribute('aria-pressed'), 'true');
    await page.keyboard.press('ArrowRight'); // clamp, no wrap or accidental keep
    assert.equal(await editor().getByRole('button', {name:/도메인만 남김/}).getAttribute('aria-pressed'), 'true');
    await page.keyboard.press('1');
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    assert.equal(await editor().getByRole('button', {name:'0 유지', exact:true}).getAttribute('aria-pressed'), 'true');
    assert.equal(writes.length, beforeArrows);

    // 2 keeps the email initial and domain, and Enter stays on the current item.
    await page.keyboard.press('2');
    assert.equal(await editor().getByRole('button', {name:/첫 글자와 도메인 남김/}).getAttribute('aria-pressed'), 'true');
    const beforeEmail = writes.length;
    await confirm();
    await assertSelected('mira@example.test');
    assert.equal(writes.length, beforeEmail + 1);
    assert.deepEqual(job.candidates.find(c => c.id === 'email').mask, [[1,4]]);
    await page.keyboard.press('Tab');
    await assertSelected('이하늘');
    await page.keyboard.press('Tab');
    await assertSelected('010-1234-5678');

    // A failed save keeps the draft and blocks movement until retry succeeds.
    await page.keyboard.press('2');
    failNextPlan = true;
    await confirm();
    await editor().getByText('검증용 저장 실패', {exact:true}).waitFor();
    const failedCount = writes.length;
    await page.keyboard.press('ArrowDown');
    await assertSelected('010-1234-5678');
    assert.equal(writes.length, failedCount);
    await confirm();
    await page.keyboard.press('Shift+Tab');
    await assertSelected('이하늘');
    await page.keyboard.press('Shift+Tab');
    await assertSelected('mira@example.test');

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
    await editor().getByRole('button', {name:'키보드 모드 켜기', exact:true}).waitFor();
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
      await page.reload({waitUntil:'networkidle'});
      await editor().waitFor();
      await editor().focus();
    };
    await showType('address','경기도 수원시 영통구 영통동 123 101동 202호');
    await page.keyboard.press('3');
    await page.keyboard.press('ArrowLeft');
    assert.equal(await editor().getByRole('button',{name:/시·도까지만 공개/}).getAttribute('aria-pressed'),'true');
    await editor().getByText('경기도'+'*'.repeat(26),{exact:true}).waitFor();
    await page.screenshot({path:'verification/semantic-presets/address-editor.png',fullPage:true});
    await confirm();
    await page.reload({waitUntil:'networkidle'});
    await editor().focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await editor().getByRole('button',{name:/시·도 \+ 시·군까지 공개/}).getAttribute('aria-pressed'),'true');
    await showType('dob','1995년 8월 17일');
    await page.keyboard.press('2');
    await editor().getByText('1995년'+'*'.repeat(7),{exact:true}).waitFor();
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
    await page.reload({waitUntil:'networkidle'});
    await page.getByText('AI 확인 질문 2개',{exact:true}).waitFor();
    const question=()=>editor().getByRole('region',{name:'AI 확인 질문'});
    await question().getByText('이 문의 연락처를 공유본에 남길까요?',{exact:true}).waitFor();
    await editor().getByText('적용 범위',{exact:true}).click();
    await editor().getByRole('radio',{name:'이 위치만',exact:true}).check();
    fs.mkdirSync('verification/context-questions',{recursive:true});
    await page.screenshot({path:'verification/context-questions/questions.png',fullPage:true});
    const beforeAnswer=writes.length;
    holdNextPlan=true;
    const answerRequest=page.waitForRequest(r=>r.url().endsWith('/plan'));
    const answerResponse=page.waitForResponse(r=>r.url().endsWith('/plan'));
    await question().getByRole('button',{name:'네, 그대로 유지',exact:true}).click();
    await answerRequest;
    assert.equal(writes.length,beforeAnswer+1);
    assert.deepEqual(writes.at(-1).candidates,[{id:'name-a',method:'keep',mask:[],confirmed:true}]);
    assert(await question().getByRole('button',{name:'네, 그대로 유지',exact:true}).isDisabled());
    releasePlan();await answerResponse;
    await question().getByText(/선택 확정됨/).waitFor();
    assert.equal(job.candidates[4].method,'full');assert.equal(job.candidates[4].confirmed,false);
    await page.getByText('AI 확인 질문 1개',{exact:true}).waitFor();
    await page.getByRole('button',{name:'질문 확인하기',exact:true}).click();
    failNextPlan=true;
    const failedAnswer=page.waitForResponse(r=>r.url().endsWith('/plan'));
    await question().getByRole('button',{name:'아니요, 전체 가림',exact:true}).click();
    await failedAnswer;await editor().getByText('검증용 저장 실패',{exact:true}).waitFor();
    assert.equal(job.candidates[4].confirmed,false);
    await page.getByText('AI 확인 질문 1개',{exact:true}).waitFor();
    const successAnswer=page.waitForResponse(r=>r.url().endsWith('/plan'));
    await question().getByRole('button',{name:'아니요, 전체 가림',exact:true}).click();
    await successAnswer;await question().getByText(/선택 확정됨/).waitFor();
    assert.equal(job.candidates[4].confirmed,true);assert.equal(job.candidates[4].method,'full');
    assert.equal(await page.getByRole('region',{name:'확인할 AI 질문'}).count(),0);

    // Unreviewed repeated values are visible when attempting export; nothing is submitted.
    job=syntheticJob();
    job.candidates[3]={...job.candidates[3],method:'keep',confirmed:true,decisionSource:'user'};
    await page.reload({waitUntil:'networkidle'});
    const beforeExport=writes.length;
    await page.getByRole('button',{name:'내보내기 →',exact:true}).click();
    assert(page.url().endsWith('/decision'));
    assert.equal(writes.length,beforeExport);
    const needed=page.locator('[data-review-needed="true"]');
    assert.equal(await needed.count(),4);
    await needed.first().getByText('검토 필요 1곳',{exact:true}).waitFor();
    await page.waitForTimeout(300);
    assert(await needed.first().evaluate(e=>{const a=e.closest('aside').getBoundingClientRect(),r=e.getBoundingClientRect();return r.top>=a.top&&r.bottom<=a.bottom&&r.top>=0&&r.bottom<=innerHeight;}));
    await page.screenshot({path:'verification/repeat-navigation/review-needed.png',fullPage:true});
    assert.equal(await editor().getByRole('button',{name:'0 유지',exact:true}).getAttribute('aria-pressed'),'true');
    // User changes inherited keep to partial. Already-confirmed keep at the other location survives.
    await editor().focus();await page.keyboard.press('2');await confirm();
    assert.equal(job.candidates[0].method,'partial');
    assert.equal(job.candidates[3].method,'keep');
    assert.equal(await needed.count(),3);
    await editor().focus();await page.keyboard.press('Tab');
    assert.equal(await page.getByRole('button',{name:'같은 정보 2번째 위치',exact:true}).getAttribute('aria-pressed'),'true');
    // A new matching unconfirmed occurrence receives the preceding partial selection on confirmation.
    job=syntheticJob();await page.reload({waitUntil:'networkidle'});
    // The middle-only preset now occupies 2; initial-only is 3.
    await editor().focus();await page.keyboard.press('3');await confirm();
    assert.deepEqual(job.candidates[0].mask,[[1,3]]);
    assert.deepEqual(job.candidates[3].mask,[[1,3]]);
    assert(job.candidates[0].confirmed&&job.candidates[3].confirmed);
    await page.screenshot({path:'verification/repeat-navigation/repeated-review.png',fullPage:true});

    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
    assert.deepEqual(external, []);
    console.log(JSON.stringify({passed:true, checks:['0 keeps without saving','Enter confirms without moving','dirty movement is blocked','Tab visits every repeated occurrence','same-type Down navigation','previous choice reused for matching unconfirmed values','red review-needed indication on blocked export','explicit keep preserved','partial mask propagated','email preset','left/right numbered draft selection','restored partial selection','address hierarchy','DOB year only','question discovery and grounding','question answers save one occurrence','unanswered full default','answer failure preserves pending state','pending save lock','save failure preserves draft','native/selection/modal/IME/repeat guards','Escape exits keyboard mode'], mockPlanRequests:writes.length, externalRequests:external.length, browserErrors:errors.length}));
  } finally {
    releasePlan?.();
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
