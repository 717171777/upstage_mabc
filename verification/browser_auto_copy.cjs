// Synthetic data only. Service calls are mocked and external network is blocked.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const BASE=process.env.GARIMI_BROWSER_URL||'http://localhost:3000';
const synthetic=()=>({
 id:'auto-copy-synthetic',version:1,status:'review',fileName:'synthetic.docx',format:'docx',
 aiEnabled:true,upstageRequired:true,expiresAt:new Date(Date.now()+3600000).toISOString(),
 units:[{id:'u1',text:'김가람 문의'}],
 candidates:[{id:'c1',type:'name',value:'김가람',unitId:'u1',start:0,end:3,method:'full',mask:[],confirmed:true,locationResolved:true}],
 metadata:[{id:'author',label:'작성자',value:'합성 작성자',action:'delete'}],metadataReviewed:false,uninspected:[],suggestions:[],
 documentType:'other',analysis:{parse:{status:'completed'},classify:{status:'completed'},extract:{status:'completed'},hermes:{status:'completed',required:true},warnings:[]},
 artifact:null,acknowledged:false,context:{recipient:null,purpose:'',keepInfo:null},
 runtime:{platform:'test',execution:'mock',model:'solar-pro4-260806',llmUsed:false}
});
(async()=>{
 const browser=await chromium.launch({headless:true});const ctx=await browser.newContext({viewport:{width:1440,height:1000}});const page=await ctx.newPage();
 let job=synthetic(),fail=false,hold=false,release,failDelete=false;const calls=[],errors=[],external=[];
 page.on('pageerror',e=>errors.push(e.message));
 await ctx.addInitScript(()=>sessionStorage.setItem('garimi-job',JSON.stringify({id:'auto-copy-synthetic',token:'mock-only'})));
 await ctx.route('**/*',r=>{if(new URL(r.request().url()).origin!==BASE){external.push(r.request().url());return r.abort();}return r.continue();});
 await ctx.route('**/api/service/**',async r=>{
  const url=new URL(r.request().url()),path=url.pathname;let body=job;
  if(path.endsWith('/health'))body={ok:true,aiConfigured:true,upstageRequired:true,model:'solar-pro4-260806'};
  else if(r.request().method()==='DELETE'){
   calls.push('delete');
   if(failDelete){failDelete=false;return r.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'합성 삭제 실패'})});}
   return r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({deleted:true})});
  }else if(path.endsWith('/plan')){
   calls.push('plan');const p=r.request().postDataJSON();assert.equal(p.version,job.version);
   job={...job,version:job.version+1,metadataReviewed:p.metadataReviewed,metadata:job.metadata.map(m=>({...m,action:p.metadataActions[m.id]})),artifact:null,acknowledged:false,status:'review'};body=job;
  }else if(path.endsWith('/render')){
   calls.push('render');assert.equal(r.request().postDataJSON().version,job.version);assert(job.candidates.every(c=>c.confirmed));assert(job.metadataReviewed);
   if(hold){hold=false;await new Promise(resolve=>release=resolve);}
   if(fail){fail=false;job={...job,status:'failed'};return r.fulfill({status:422,contentType:'application/json',body:JSON.stringify({error:'합성 검사 실패'})});}
   job={...job,status:'validated',artifact:{sha256:'hash-'+job.version,version:job.version,validationVersion:job.version,checks:[{name:'저장 파일 검사',passed:true}]}};body=job;
  }else if(path.endsWith('/preview')){
   const copy=url.searchParams.get('variant')==='copy';if(copy){assert.equal(job.status,'validated');calls.push('copy-preview');}
   const units=copy?[{id:'u1',text:'*** 문의'}]:job.units;
   body={format:'docx',units,blocks:[{kind:'paragraph',unitId:'u1'}],metadata:[],uninspected:[]};
  }else if(!path.endsWith('/jobs/auto-copy-synthetic'))throw Error('Unexpected service call '+path);
  return r.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
 });
 const count=action=>calls.filter(x=>x===action).length;
 const saved=()=>page.getByText('실제 저장된 사본',{exact:true}).waitFor();
 try{
  hold=true;await page.goto(BASE+'/export');
  await page.getByText('사본을 만들고 검사하고 있어요',{exact:true}).waitFor();
  assert.equal(count('render'),1);assert.equal(count('copy-preview'),0);
  assert(await page.getByRole('button',{name:'처리 중…',exact:true}).isDisabled());
  release();await saved();assert.equal(count('plan'),1);assert.equal(count('render'),1);
  assert(await page.getByRole('button',{name:'확인하고 다운로드',exact:true}).isDisabled());
  await page.getByRole('checkbox',{name:'유지한 정보와 검사 범위를 확인했습니다.',exact:true}).check();
  assert(await page.getByRole('button',{name:'확인하고 다운로드',exact:true}).isEnabled());
  // Existing validated copy is reused; no render on refresh.
  await page.reload({waitUntil:'networkidle'});await saved();await page.waitForTimeout(650);assert.equal(count('render'),1);
  // Metadata changes invalidate the artifact and acknowledgement and prepare a new copy.
  await page.locator('summary').filter({hasText:'문서 속성'}).click();
  await page.getByLabel('작성자 처리',{exact:true}).selectOption('keep');await saved();
  await page.waitForTimeout(650);assert.equal(count('render'),2);assert.equal(count('plan'),2);
  assert.equal(job.metadata[0].action,'keep');assert(await page.getByRole('button',{name:'확인하고 다운로드',exact:true}).isDisabled());
  await page.screenshot({path:'verification/auto-copy/ready.png',fullPage:true});
  // Failure after the metadata version increment must not create a retry loop.
  job=synthetic();fail=true;await page.reload({waitUntil:'networkidle'});
  await page.getByText('저장 사본을 준비하지 못했어요',{exact:true}).waitFor();
  const afterFailure=count('render');await page.waitForTimeout(1200);assert.equal(count('render'),afterFailure);
  await page.getByRole('button',{name:'사본 생성 다시 시도',exact:true}).click();await saved();assert.equal(count('render'),afterFailure+1);
  // An unconfirmed or unresolved item never renders.
  for(const field of ['confirmed','locationResolved']){
   job=synthetic();job.candidates[0][field]=false;const n=count('render');
   await page.reload({waitUntil:'networkidle'});await page.getByText('먼저 가림 검토를 마쳐 주세요',{exact:true}).waitFor();
   await page.waitForTimeout(650);assert.equal(count('render'),n);assert.equal(await page.getByText('실제 저장된 사본',{exact:true}).count(),0);
  }
  // Restart is an explicit action: failed deletion preserves the current work.
  job=synthetic();await page.reload({waitUntil:'networkidle'});await saved();
  const restart=page.getByRole('button',{name:'새로 시작하기',exact:true});
  assert.equal(await restart.evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(180, 35, 24)');
  failDelete=true;await restart.click();await page.getByRole('alert').filter({hasText:'합성 삭제 실패'}).waitFor();
  assert(page.url().endsWith('/export'));assert(await page.evaluate(()=>sessionStorage.getItem('garimi-job')));
  await restart.click();await page.waitForURL(BASE+'/');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('garimi-job')),null);
  await page.getByRole('heading',{name:'공유할 문서, 함께 준비해요',exact:true}).waitFor();
  assert.equal(count('delete'),2);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log(JSON.stringify({passed:true,autoGenerate:true,reuseValidated:true,metadataRebuild:true,noRetryLoop:true,explicitRetry:true,unreviewedBlocked:true,downloadAcknowledgement:true,restartClearsWorkspace:true,failedRestartPreservesWorkspace:true,renderCount:count('render'),externalRequests:external.length,browserErrors:errors.length}));
 }finally{release?.();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
