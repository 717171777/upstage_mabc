/* eslint-disable @typescript-eslint/no-require-imports */
// Synthetic service responses; real page interactions, no external requests.
const {chromium, expect} = require('playwright/test');
const assert = require('node:assert/strict');
const BASE = process.env.GARIMI_BROWSER_URL || 'http://localhost:3107';

(async () => {
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  let fail = false;
  const calls = [], errors = [];
  let job = {
    id: 'synthetic-full', version: 1, status: 'review', fileName: '합성-개인정보.docx', format: 'docx',
    aiEnabled: true, expiresAt: new Date(Date.now() + 3600000).toISOString(),
    units: [{id: 'u1', text: '참가자: 김가람 연락처: 010-2345-6789'}],
    candidates: [
      {id: 'c1', type: 'name', value: '김가람', unitId: 'u1', start: 5, end: 8, method: 'keep', mask: [], confirmed: true, locationResolved: true},
      {id: 'c2', type: 'phone', value: '010-2345-6789', unitId: 'u1', start: 14, end: 27, method: 'partial', mask: [[4,8]], confirmed: true, locationResolved: true},
    ], metadata: [{id: 'author', label: '작성자', value: '합성 작성자', action: 'keep'}],
    metadataReviewed: true, uninspected: [], suggestions: [], documentType: 'personnel_roster',
    analysis: {parse: {status: 'completed'}, classify: {status: 'completed'}, extract: {status: 'completed'}, hermes: {status: 'completed', required: true}, warnings: []},
    artifact: null, acknowledged: false, context: {recipient: null, purpose: '', keepInfo: null},
  };
  page.on('pageerror', error => errors.push(error.message));
  await context.addInitScript(() => sessionStorage.setItem('garimi-job', JSON.stringify({id: 'synthetic-full', token: 'synthetic-token'})));
  await context.route('**/*', route => new URL(route.request().url()).origin === BASE ? route.continue() : route.abort());
  await context.route('**/api/service/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body = job;
    if (path.endsWith('/plan')) {
      const payload = route.request().postDataJSON();
      calls.push(payload);
      assert.equal(payload.version, job.version);
      if (fail) {
        fail = false;
        return route.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({error: '합성 저장 실패'})});
      }
      job = {...job, version: job.version + 1, fullRedaction: payload.fullRedaction, artifact: null, acknowledged: false};
      if (job.fullRedaction) {
        job.candidates = job.candidates.map(c => ({...c, method: 'full', mask: [], confirmed: true}));
        job.metadata = job.metadata.map(m => ({...m, action: 'delete'}));
      }
      body = job;
    } else if (path.endsWith('/preview')) {
      body = {format: 'docx', units: job.units, blocks: [{kind: 'paragraph', unitId: 'u1'}], metadata: [], uninspected: []};
    } else if (!path.endsWith('/jobs/synthetic-full')) {
      throw new Error('Unexpected endpoint ' + path);
    }
    return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
  });
  try {
    await page.goto(BASE + '/decision');
    const toggle = page.getByRole('switch', {name: '모든 개인정보 전체 가림'});
    await expect(toggle).toHaveAttribute('aria-checked', 'false');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    assert(job.candidates.every(c => c.method === 'full' && c.confirmed));
    assert.equal(job.metadata[0].action, 'delete');
    await expect(page.getByRole('button', {name: '0 유지', exact: true})).toBeDisabled();
    await expect(page.getByRole('button', {name: '전체 가림으로 바로 받기', exact: true})).toBeEnabled();
    await page.reload();
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    await page.screenshot({path: '/tmp/garimi-full-redaction-desktop.png', fullPage: true});
    // A failed save must leave the switch and saved policy enabled.
    fail = true;
    await toggle.click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'false');
    assert(job.candidates.every(c => c.method === 'full'));
    await expect(page.getByRole('button', {name: '0 유지', exact: true})).toBeEnabled();
    // Keyboard operation and mobile layout.
    await page.setViewportSize({width: 390, height: 844});
    await toggle.focus();
    await page.keyboard.press('Space');
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    await page.screenshot({path: '/tmp/garimi-full-redaction-mobile.png', fullPage: true});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    assert.equal(calls.length, 4);
    assert.deepEqual(errors, []);
    console.log('PASS: toggle, persistence, full decisions, error rollback, off, keyboard, mobile');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
