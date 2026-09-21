'use client';

import {useApp} from '@/contexts/AppContext';
import {ServiceShell, EmptyJob, LegacyJobNotice} from '@/components/service-shell';
import {DocumentView} from '@/components/document-view';
import {useLinkedDocumentScroll} from '@/components/use-linked-document-scroll';
import {PII_LABELS, type Job} from '@/lib/service';
import {useState, useRef, useEffect, useCallback, useMemo} from 'react';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {isUpstageComplete} from '@/lib/workspace';

export default function ExportPage() {
  const {job}=useApp();
  if(job && (!job.aiEnabled || job.upstageReanalysisRequired))return <LegacyJobNotice/>;
  return job?<ExportWorkspace key={job.id} job={job}/>:<EmptyJob/>;
}

function ExportWorkspace({job}:{job:Job}) {
  const {busy,mutate,download,setError,erase}=useApp();
  const router=useRouter();
  const [restarting,setRestarting]=useState(false);
  const [choiceState,setChoiceState]=useState<{version:number;values:Record<string,'delete'|'keep'>}|null>(null);
  const choices=useMemo(()=>choiceState?.version===job.version?choiceState.values:Object.fromEntries(job.metadata.map(m=>[m.id,m.action])),[choiceState,job.version,job.metadata]);
  const [filename,setFilename]=useState(()=>job.fileName.replace(/\.[^.]+$/,'')+'-공유본.'+job.format);
  const [checkedArtifact,setCheckedArtifact]=useState('');
  const artifactKey=job.artifact?`${job.version}:${job.artifact.sha256}`:'';
  const checked=!!artifactKey&&checkedArtifact===artifactKey;
  const [working,setWorking]=useState(false);
  const [notice,setNotice]=useState('');
  const [pageNumber,setPageNumber]=useState(1);
  const [scrollLinked,setScrollLinked]=useState(true);
  const [comparisonZoom,setComparisonZoom]=useState(100);
  const lock=useRef(false);
  const attempted=useRef('');
  const [copyError,setCopyError]=useState('');
  const inputKey=JSON.stringify([job.version,choices]);
  const dirty=!!job&&job.metadata.some(m=>choices[m.id]!==m.action);
  const validated=!!job&&isUpstageComplete(job)&&job.status==='validated'&&!!job.artifact&&job.artifact.version===job.version&&job.artifact.validationVersion===job.version&&job.artifact.checks.every(c=>c.passed)&&!dirty;
  const allReady=!!job&&isUpstageComplete(job)&&job.candidates.every(c=>c.locationResolved)&&!job.analysis.incomplete&&!['analyzing','rendering'].includes(job.status);
  const disabled=busy||working;
  const retained=job?.candidates.filter(c=>c.method==='keep').length??0;
  const keptMetadata=Object.values(choices).filter(v=>v==='keep').length;
  const nameError=!filename.trim()?'파일 이름을 입력해 주세요.':filename.trim().length>160||/[\\/\x00-\x1f\x7f]/.test(filename)?'파일 이름에 사용할 수 없는 문자가 있습니다.':job&&!filename.trim().toLowerCase().endsWith('.'+job.format)?`확장자는 .${job.format}여야 합니다.`:'';
  const report=validated&&job?.aiReview?.version===job?.version&&job.aiReview.sha256===job.artifact?.sha256?job.aiReview:null;
  const reportRunning=report?.status==='running';
  const {originalRef,copyRef}=useLinkedDocumentScroll(scrollLinked&&validated,`${artifactKey}:${validated}:${pageNumber}:${comparisonZoom}`);

  const createCopy=useCallback(async()=>{
    if(!job||disabled||lock.current||!allReady)return;lock.current=true;attempted.current=inputKey;setWorking(true);setCopyError('');setError(null);setNotice('');
    try{
      if(job.candidates.some(c=>!c.confirmed)){
        const prepared=await mutate('prepare-export',{});
        attempted.current=JSON.stringify([prepared.version,choices]);
      }
      if(dirty||!job.metadataReviewed){
        const planned=await mutate('plan',{metadataActions:choices,metadataReviewed:true});
        attempted.current=JSON.stringify([planned.version,choices]);
      }
      const updated=await mutate('render',{});
      if(updated.status!=='validated'||!updated.artifact)throw new Error('저장 사본 검사가 완료되지 않았습니다.');
      setNotice('저장한 사본을 다시 열어 검사했습니다. 좌우 문서를 비교해 주세요.');
    }catch(e){const message=e instanceof Error?e.message:String(e);setCopyError(message);setError(message);}finally{lock.current=false;setWorking(false);}
  },[job,disabled,allReady,inputKey,dirty,choices,mutate,setError]);
  useEffect(()=>{
    if(validated||!allReady||disabled||attempted.current===inputKey)return;
    // Debounce metadata choices; the ref also prevents duplicate Strict Mode requests.
    const timer=setTimeout(()=>{void createCopy();},300);
    return()=>clearTimeout(timer);
  },[validated,allReady,disabled,inputKey,createCopy]);
  const getFile=async()=>{
    if(!job?.artifact||!validated||disabled||lock.current||nameError||(!job.acknowledged&&!checked))return;
    lock.current=true;setWorking(true);setError(null);setNotice('');
    try{
      if(!job.acknowledged)await mutate('ack',{sha256:job.artifact.sha256,validationVersion:job.artifact.validationVersion});
      const blob=await download(filename.trim());const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename.trim();a.click();setTimeout(()=>URL.revokeObjectURL(url),1500);setNotice('공유용 사본을 다운로드했습니다.');
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{lock.current=false;setWorking(false);}
  };
  const startOver=async()=>{
    if(disabled||lock.current||job.status==='rendering'||job.aiReview?.status==='running')return;
    lock.current=true;setRestarting(true);setWorking(true);setError(null);
    try{await erase();router.replace('/');}
    catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{lock.current=false;setRestarting(false);setWorking(false);}
  };
  const reviewCopy=async()=>{
    if(!job?.artifact||!validated||disabled||!job.aiEnabled||reportRunning)return;
    try{await mutate('ai-review',{sha256:job.artifact.sha256});setNotice('저장 사본에 남아 있는 정보를 AI가 검토합니다.');}catch(e){setError(e instanceof Error?e.message:String(e));}
  };
  if(!job)return <EmptyJob/>;

  return <ServiceShell step={3} title="비교하고 내보내기">
    {!isUpstageComplete(job)&&<p role="status" className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">{job.processingBlocked||'Upstage 분석이 완료되지 않았습니다. 가림 검토로 돌아가 AI 검토를 다시 실행해 주세요.'}</p>}
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
      <Link href="/decision" className="text-sm text-slate-500">← 가림 검토</Link>
      <button onClick={getFile} disabled={disabled||!validated||!!nameError||(!job.acknowledged&&!checked)} className="rounded-xl bg-blue-600 px-5 py-3 text-sm font-semibold text-white disabled:opacity-40">{working?'처리 중…':job.acknowledged?'파일 다운로드':'확인하고 다운로드'}</button>
    </div>
    {job.acknowledgmentMode==='ai_automatic'&&validated&&<div className="mb-4 rounded-xl bg-blue-50 px-4 py-3 text-sm text-blue-800">가림·유지 판단과 기본 전체 가림을 적용하고 저장 사본 검사를 통과했습니다. 유지한 정보도 아래에서 비교할 수 있습니다.</div>}
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div><h2 className="text-sm font-semibold text-slate-800">원본과 저장 사본 비교</h2><p className="mt-1 text-xs text-slate-500">{scrollLinked?'어느 쪽을 스크롤해도 같은 위치를 함께 보여줍니다.':'각 문서를 따로 움직일 수 있습니다.'}</p></div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" role="switch" aria-checked={scrollLinked} aria-label="스크롤 연동" onClick={()=>setScrollLinked(value=>!value)} className={`flex min-h-11 items-center gap-2 rounded-lg border px-3 text-sm ${scrollLinked?'border-blue-200 bg-blue-50 text-blue-700':'border-slate-200 text-slate-600'}`}><span aria-hidden="true">↕</span> 스크롤 연동 {scrollLinked?'켜짐':'꺼짐'}</button>
        <label className="flex min-h-11 items-center gap-2 rounded-lg border border-slate-200 px-3 text-xs text-slate-500"><span>확대</span><select aria-label="비교 문서 확대" value={comparisonZoom} onChange={event=>setComparisonZoom(Number(event.target.value))} className="min-h-10 bg-transparent text-sm text-slate-700 outline-none focus-visible:ring-2 focus-visible:ring-blue-400">{[80,100,125,150,175,200].map(value=><option key={value} value={value}>{value===100?'너비에 맞춤':`${value}%`}</option>)}</select></label>
      </div>
    </div>
    <div className="grid items-start gap-5 md:grid-cols-2" data-testid="document-comparison">
      <section className="min-w-0"><h2 className="mb-2 text-sm font-semibold text-slate-700">가리기 전 <span className="font-normal text-slate-400">· 원본</span></h2><DocumentView job={job} variant="original" readOnlyOriginal compact pageNumber={pageNumber} onPageChange={setPageNumber} viewportRef={originalRef} zoomPercent={comparisonZoom}/></section>
      <section className="min-w-0"><h2 className="mb-2 text-sm font-semibold text-slate-700">가린 후 <span className="font-normal text-slate-400">· 저장 사본</span></h2>{validated?<DocumentView job={job} variant="copy" compact pageNumber={pageNumber} onPageChange={setPageNumber} viewportRef={copyRef} zoomPercent={comparisonZoom}/>:<div role="status" aria-live="polite" className="flex min-h-[420px] flex-col items-center justify-center border border-dashed border-slate-300 bg-white p-8 text-center">
        <h3 className="text-base font-semibold">{copyError?'저장 사본을 준비하지 못했어요':working||job.status==='rendering'?'사본을 만들고 검사하고 있어요':allReady?'저장 사본을 자동으로 준비합니다':'문서 분석과 원문 위치를 확인해 주세요'}</h3>
        <p className="my-3 max-w-sm text-sm leading-relaxed text-slate-500">{copyError||'선택한 가림과 문서 속성 설정을 적용하고, 실제 저장 파일을 다시 열어 검사한 뒤 여기에 보여드립니다.'}</p>
        {copyError&&<button onClick={createCopy} disabled={!allReady||disabled} className="mt-2 bg-blue-600 px-5 py-3 text-sm font-medium text-white disabled:opacity-40">사본 생성 다시 시도</button>}
        {!allReady&&!working&&job.status!=='rendering'&&<Link href="/decision" className="mt-4 text-xs text-blue-600">{isUpstageComplete(job)?'원문 위치 연결하기 →':'문서 분석을 완료해 주세요 →'}</Link>}
      </div>}</section>
    </div>
    <div className="mt-5 space-y-3">
      <details className="rounded-xl border border-slate-200 bg-white px-4 py-3"><summary className="cursor-pointer text-sm font-medium">문서 속성 <span className="ml-2 text-xs font-normal text-slate-500">{job.metadata.length-keptMetadata}개 삭제 · {keptMetadata}개 유지</span></summary><p className="mb-3 mt-3 text-xs text-slate-500">작성자 등 개인정보가 남을 수 있는 속성은 기본 삭제합니다. 필요한 항목만 유지하세요.</p>{job.metadata.length?job.metadata.map(m=><div key={m.id} className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 py-2 text-sm"><div className="min-w-0"><p className="text-xs text-slate-400">{m.label}</p><p className="break-all">{m.value}</p></div><select aria-label={`${m.label} 처리`} value={choices[m.id]??m.action} disabled={disabled} onChange={e=>setChoiceState({version:job.version,values:{...choices,[m.id]:e.target.value as 'delete'|'keep'}})} className="rounded-lg border border-slate-200 px-3 py-2"><option value="delete">삭제</option><option value="keep">유지</option></select></div>):<p className="text-sm text-slate-500">발견된 문서 속성이 없습니다.</p>}</details>
      {validated&&<section className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-semibold">가린 사본 AI 검토</h2><p className="mt-1 text-xs text-slate-500">남아 있는 정보와 누락 의심 항목을 추가로 확인합니다.</p></div><button onClick={reviewCopy} disabled={disabled||!job.aiEnabled||reportRunning} className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm text-blue-700 disabled:opacity-40">{reportRunning?'AI 검토 중…':report?'다시 AI 검토':'AI 검토 시작'}</button></div><p className="mt-3 text-xs text-slate-400">Upstage에 저장 사본, Hermes·Claude(Anthropic)에 필요한 근거를 보냅니다.</p>
        {report&&report.status!=='running'&&<div aria-live="polite" className="mt-4 border-t border-slate-100 pt-3"><p className={`text-sm font-medium ${report.status==='completed'?'text-slate-700':'text-amber-700'}`}>{report.status==='completed'?'AI 검토를 마쳤습니다':report.status==='partial'?'일부 검토가 끝나지 않았습니다':'AI 검토를 완료하지 못했습니다'}</p>{report.findings.length?<><p className="mt-1 text-xs text-slate-500">남아 있는 정보 {report.findings.length}개 · 의도한 유지인지 확인하세요.</p><div className="mt-3 max-h-60 space-y-3 overflow-auto">{report.findings.map((f,i)=><div key={`${f.id}-${i}`} className="rounded-lg bg-slate-50 p-3 text-sm"><p><span className="mr-2 text-xs text-slate-500">{PII_LABELS[f.type]}</span>{f.value}</p><p className="mt-1 text-xs text-slate-500">{f.reason}</p>{f.plannedKeep&&<p className="mt-1 text-xs text-blue-600">유지한 값과 일치 · 위치별 확인 필요</p>}</div>)}</div><Link href="/decision" className="mt-3 inline-block text-xs text-blue-600">가림 검토에서 수정 →</Link></>:report.status==='completed'?<p className="mt-2 text-xs text-slate-500">추가 후보를 찾지 못했습니다. 누락이 없음을 보장하지 않습니다.</p>:null}{report.warnings.map((w,i)=><p key={i} className="mt-2 text-xs text-amber-700">{w}</p>)}</div>}
      </section>}
      <details className="rounded-xl border border-slate-200 bg-white px-4 py-3"><summary className="cursor-pointer text-sm font-medium">파일 이름·검사 결과</summary><label className="mt-3 block text-xs text-slate-500">파일 이름<input aria-label="파일 이름" value={filename} disabled={disabled} onChange={e=>setFilename(e.target.value)} className="mt-1 block w-full rounded-lg border border-slate-200 p-2 text-sm text-slate-800"/></label>{nameError&&<p className="mt-1 text-xs text-red-600">{nameError}</p>}{validated&&job.artifact?<div className="mt-4 text-xs text-slate-500"><p className="font-medium text-slate-700">저장 사본 재검사 통과</p>{job.artifact.checks.map((c,i)=><p key={i} className="mt-1">{c.detail||c.name} · {c.passed?'통과':'확인 필요'}</p>)}<p className="mt-2 break-all font-mono text-[10px]">SHA-256 {job.artifact.sha256}</p></div>:<p className="mt-3 text-xs text-slate-500">아직 현재 설정의 저장 사본을 검사하지 않았습니다.</p>}</details>
      {validated&&<div className="rounded-xl bg-slate-100 p-4 text-sm"><p className="text-slate-600">유지한 정보 {retained}곳 · 유지한 문서 속성 {keptMetadata}개</p>{job.uninspected.length>0&&<p className="mt-2 text-amber-700">검사하지 못한 영역: {job.uninspected.join(', ')}</p>}{!job.acknowledged&&<label className="mt-3 flex items-start gap-2"><input type="checkbox" checked={checked} onChange={e=>setCheckedArtifact(e.target.checked?artifactKey:'')} className="mt-1"/><span>유지한 정보와 검사 범위를 확인했습니다.</span></label>}{job.acknowledged&&<p className="mt-2 text-xs text-slate-500">{job.acknowledgmentMode?.endsWith('_automatic')?'자동 적용으로 다운로드가 허용된 사본입니다.':'확인한 사본을 다운로드할 수 있습니다.'}</p>}</div>}
    </div>
    <p aria-live="polite" className="mt-4 text-sm text-blue-700">{notice}</p>
    <section aria-label="새 문서로 시작" className="mt-8 flex flex-wrap items-center justify-between gap-4 border-t border-slate-200 pt-6">
      <p className="max-w-xl text-xs leading-relaxed text-slate-500">새로 시작하면 현재 작업 공간과 저장 사본이 삭제됩니다. 컴퓨터의 원본과 이미 다운로드한 파일은 그대로 유지됩니다.</p>
      <button type="button" onClick={startOver} disabled={disabled||job.status==='rendering'||job.aiReview?.status==='running'} className="min-h-11 bg-red-600 px-6 py-3 text-sm font-semibold text-white hover:bg-red-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 disabled:opacity-40">{restarting?'정리 중…':'새로 시작하기'}</button>
    </section>
  </ServiceShell>;
}
