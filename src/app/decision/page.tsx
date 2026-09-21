'use client';

import {useApp} from '@/contexts/AppContext';
import {ServiceShell, AnalysisStatus, EmptyJob, LegacyJobNotice} from '@/components/service-shell';
import {DocumentView} from '@/components/document-view';
import {ReviewCategories} from '@/components/review-categories';
import {changedPreviewDecisions, isUserDecision} from '@/lib/review-flow';
import {CandidateEditor} from '@/components/candidate-editor';
import {Job, Candidate, Method, PiiType, PII_LABELS, DOCTYPE_LABELS} from '@/lib/service';
import {useState, useRef, useEffect, useMemo, useCallback} from 'react';
import {useRouter} from 'next/navigation';
import {repeatedReviewOrder} from '@/lib/repeat-decisions';
import {isUpstageComplete} from '@/lib/workspace';
import {applyPreviewDecisions, type PreviewDecision, type ReviewViewMode} from '@/lib/review-preview';
import Link from 'next/link';

type Decision = {id:string; method:Method; mask:[number,number][]; confirmed:boolean};
type Hit = {unitId:string; start:number; end:number; value:string};
const groupKey = (c:Candidate) => JSON.stringify([c.type,c.value]);
function findOccurrences(job:Job, value:string): Hit[] {
  if (!value) return [];
  return job.units.flatMap(u => {
    const hits:Hit[]=[]; let from=0;
    while (from < u.text.length) {
      const pos=u.text.indexOf(value,from); if(pos<0) break;
      const start=Array.from(u.text.slice(0,pos)).length;
      hits.push({unitId:u.id,start,end:start+Array.from(value).length,value}); from=pos+value.length;
    }
    return hits;
  });
}
function overlap(candidates:Candidate[], hit:Hit) {return candidates.find(c => c.unitId===hit.unitId && c.start!==null && c.end!==null && c.start<hit.end && c.end>hit.start);}

export default function DecisionPage() {
  const {job,busy,mutate,setError}=useApp();
  const router=useRouter();
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [filter,setFilter]=useState('all');
  const [search,setSearch]=useState('');
  const [activeType,setActiveType]=useState<PiiType>('name');
  const [manualOpen,setManualOpen]=useState(false);
  const [manualQuery,setManualQuery]=useState('');
  const [manualType,setManualType]=useState<PiiType>('name');
  const [range,setRange]=useState<Hit|null>(null);
  const [undo,setUndo]=useState<Decision[][]>([]);
  const [working,setWorking]=useState(false);
  const [notice,setNotice]=useState('');
  const [viewMode,setViewMode]=useState<ReviewViewMode>('detected');
  const [keyboardMode,setKeyboardMode]=useState(false);
  const [draftPreview,setDraftPreview]=useState<{key:string;decisions:PreviewDecision[];valid:boolean}|null>(null);
  const [editedPreviewKey,setEditedPreviewKey]=useState<string|null>(null);
  const lock=useRef(false);
  const panelRef=useRef<HTMLElement>(null);
  const documentWorkspaceRef=useRef<HTMLDivElement>(null);
  const candidates=useMemo(()=>job?.candidates??[],[job?.candidates]);
  const selected=candidates.find(c=>c.id===selectedId)??null;
  const previewKey=`${job?.id}:${job?.version}:${selected?.id}`;
  const previewDecisions=!manualOpen && draftPreview?.key===previewKey ? draftPreview.decisions : [];
  const previewCandidates=applyPreviewDecisions(candidates,previewDecisions);
  const draftChanges=editedPreviewKey===previewKey?changedPreviewDecisions(candidates,previewDecisions):[];
  const draftInvalid=editedPreviewKey===previewKey&&draftPreview?.key===previewKey&&!draftPreview.valid;
  const previewPending=draftChanges.length>0||draftInvalid;
  const updatePreview=useCallback((decisions:PreviewDecision[],valid:boolean)=>setDraftPreview({key:previewKey,decisions,valid}),[previewKey]);
  const startPreview=useCallback(()=>{setViewMode('masked');setEditedPreviewKey(previewKey);},[previewKey]);
  const selectedSuggestion=job?.suggestions.find(s=>s.candidateId===selected?.id&&s.question)??job?.suggestions.find(s=>s.candidateId===selected?.id);
  const pendingQuestions=candidates.filter(c=>!isUserDecision(c) && job?.suggestions.some(s=>s.candidateId===c.id && s.question));
  const initialized=useRef<string|null>(null);
  const analyzing=job?.status==='analyzing'||job?.status==='rendering';
  const disabled=busy||working||analyzing;
  useEffect(()=>{if(job && initialized.current!==job.id){initialized.current=job.id;setSelectedId(null);setActiveType(job.candidates[0]?.type??'name');setUndo([]);}},[job]);
  useEffect(()=>{
    const panel=panelRef.current;
    if(!panel)return;
    panel.scrollTop=0;
    if(selectedId||manualOpen){
      if(window.matchMedia('(max-width: 899px)').matches)panel.scrollIntoView({block:'start'});
      else if(panel.getBoundingClientRect().top>200)documentWorkspaceRef.current?.scrollIntoView({block:'start'});
    }
  },[selectedId,manualOpen]);

  const groups=useMemo(()=>{
    const map=new Map<string,Candidate[]>();
    for(const c of candidates)map.set(groupKey(c),[...(map.get(groupKey(c))??[]),c]);
    return [...map.values()];
  },[candidates]);
  const navigationCandidates=useMemo(()=>{
    const unitOrder=new Map(job?.units.map((unit,index)=>[unit.id,index]));
    return [...candidates].sort((a,b)=>(unitOrder.get(a.unitId??'')??Infinity)-(unitOrder.get(b.unitId??'')??Infinity)||(a.start??Infinity)-(b.start??Infinity));
  },[candidates,job?.units]);
  const matchesFilter=useCallback((c:Candidate)=>(filter==='all'||(filter==='custom'&&isUserDecision(c))||(filter==='keep'&&c.method==='keep')||(filter==='mask'&&c.method!=='keep')) && (!search || `${c.value} ${PII_LABELS[c.type]}`.toLowerCase().includes(search.toLowerCase())),[filter,search]);
  const visible=useMemo(()=>groups.filter(g=>g[0].type===activeType&&g.some(matchesFilter)),[groups,activeType,matchesFilter]);
  const bulkItems=visible.flat().filter(c=>c.locationResolved && matchesFilter(c));
  const currentGroup=selected?groups.find(g=>groupKey(g[0])===groupKey(selected))??[]:[];
  const customized=candidates.filter(isUserDecision).length;
  const unresolved=candidates.filter(c=>!c.locationResolved).length;
  const canExport=!!job && isUpstageComplete(job) && !unresolved && !disabled;
  const proceedReason=!job||!isUpstageComplete(job)?'문서 분석을 완료한 뒤 다음 단계로 이동할 수 있어요.':unresolved?`원문 위치 ${unresolved}곳을 연결해 주세요.`:'';
  const hits=job?findOccurrences(job,manualQuery):[];

  const save=useCallback(async(decisions:Decision[])=>{
    if(busy||lock.current||!job)throw new Error('저장이 끝난 뒤 다시 시도해 주세요.');
    lock.current=true;setWorking(true);
    const snapshot=job.candidates.filter(c=>decisions.some(d=>d.id===c.id)).map(c=>({id:c.id,method:c.method,mask:c.mask,confirmed:c.confirmed}));
    try{const updated=await mutate('plan',{candidates:decisions});setUndo(stack=>[...stack,snapshot].slice(-20));return updated;}
    finally{lock.current=false;setWorking(false);}
  },[busy,job,mutate]);
  const selectCandidate=async(id:string|null,type?:PiiType)=>{
    if(disabled)return;
    if(draftInvalid){setNotice('일부 가림의 범위를 완성하거나 다른 방법을 골라 주세요.');return;}
    try{
      if(draftChanges.length)await save(draftChanges);
      const next=candidates.find(c=>c.id===id);
      setSelectedId(id);setActiveType(next?.type??type??activeType);setManualOpen(false);setEditedPreviewKey(null);
    }catch(e){setError(e instanceof Error?e.message:String(e));}
  };
  const openManual=async(hit?:Hit)=>{
    if(disabled)return;
    if(draftInvalid){setNotice('일부 가림의 범위를 완성하거나 다른 방법을 골라 주세요.');return;}
    try{
      if(draftChanges.length)await save(draftChanges);
      setEditedPreviewKey(null);setViewMode('detected');
      if(hit){setRange(hit);setManualQuery(hit.value);}
      else if(selected&&!selected.locationResolved)setManualQuery(selected.value);
      setManualOpen(true);
    }catch(e){setError(e instanceof Error?e.message:String(e));}
  };
  const navigate=(kind:'occurrence'|'group',reverse:boolean)=>{
    if(!selected)return;
    const step=reverse?-1:1;
    if(kind==='occurrence'){
      const sameType=navigationCandidates.filter(c=>c.type===selected.type);
      const index=sameType.findIndex(c=>c.id===selected.id);
      void selectCandidate(sameType[(index+step+sameType.length)%sameType.length].id);
      setNotice(sameType.length===1?'이 종류의 정보는 문서 안에 한 곳만 있습니다.':`${PII_LABELS[selected.type]}의 ${reverse?'이전':'다음'} 정보로 이동했습니다.`);
    }else{
      const ordered=repeatedReviewOrder(navigationCandidates);
      const index=ordered.findIndex(c=>c.id===selected.id);
      const next=ordered[(index+step+ordered.length)%ordered.length];
      void selectCandidate(next.id);
      const same=ordered.filter(c=>c.type===next.type && c.value===next.value);
      setNotice(`${PII_LABELS[next.type]} · 같은 값 ${same.findIndex(c=>c.id===next.id)+1}/${same.length}번째 위치`);

    }
  };
  const undoLast=useCallback(async()=>{
    if(disabled||!undo.length)return;
    try{await mutate('plan',{candidates:undo[undo.length-1].filter(d=>candidates.some(c=>c.id===d.id))});setUndo(s=>s.slice(0,-1));setSelectedId(null);setNotice('직전 선택을 되돌렸습니다.');}catch(e){setError(e instanceof Error?e.message:String(e));}
  },[disabled,undo,mutate,candidates,setError]);
  useEffect(()=>{
    const key=(e:KeyboardEvent)=>{if(e.defaultPrevented || (e.target as HTMLElement).closest('input,textarea,select,[contenteditable="true"]'))return;if((e.metaKey||e.ctrlKey)&&e.key==='z'){e.preventDefault();void undoLast();}};
    window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);
  },[undoLast]);
  const addHits=async(items:Hit[])=>{
    if(disabled||lock.current||!job)return;lock.current=true;setWorking(true);
    try{
      let current=job;
      for(const hit of items){const existing=overlap(current.candidates,hit);if(existing){setSelectedId(existing.id);setActiveType(existing.type);continue;}
        current=await mutate('manual',{type:manualType,...hit});const added=current.candidates.find(c=>c.unitId===hit.unitId&&c.start===hit.start&&c.end===hit.end);if(added){setSelectedId(added.id);setActiveType(added.type);}
      }
      setRange(null);setManualOpen(false);
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{lock.current=false;setWorking(false);}
  };
  const requestExport=async()=>{
    if(disabled||lock.current||!job)return;
    if(draftInvalid){setNotice('일부 가림의 범위를 완성하거나 다른 방법을 골라 주세요.');return;}
    if(!canExport){
      const first=navigationCandidates.find(c=>!c.locationResolved);
      if(first)await selectCandidate(first.id);
      setNotice(proceedReason);return;
    }
    lock.current=true;setWorking(true);setError(null);
    try{
      await mutate('prepare-export',{candidates:draftChanges});
      router.push('/export');
    }catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{lock.current=false;setWorking(false);}
  };
  const rerunAI=async()=>{if(!job||disabled||!job.aiEnabled)return;try{await mutate('retry-analysis',{});setNotice('저장된 결과를 재사용하고 남은 분석을 진행합니다.');}catch(e){setError(e instanceof Error?e.message:String(e));}};
  if(!job)return <EmptyJob/>;
  if(!job.aiEnabled || job.upstageReanalysisRequired)return <LegacyJobNotice/>;

  return <ServiceShell step={2} title="가림 검토">
    <div className="sticky top-0 z-20 -mx-1 mb-5 rounded-2xl border border-slate-200 bg-white/95 p-4 backdrop-blur">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><p className="font-semibold text-slate-800">개인정보 {candidates.length}곳 · 직접 설정 {customized}곳</p><p className="mt-1 text-xs text-slate-500">검토는 선택 사항이에요. 필요한 정보만 바꾸고 다음으로 이동하세요.</p></div>
        <button onClick={()=>void requestExport()} disabled={disabled} className="rounded-xl bg-blue-600 px-5 py-3 text-sm font-semibold text-white disabled:opacity-40">{working?'설정 반영 중…':'이 설정으로 다음 →'}</button>
      </div>
      <p className="mt-3 text-xs text-slate-500">{proceedReason||'수정하지 않은 항목은 현재 기본 설정을 사용합니다. 다음 단계에서 원본과 공유본을 비교할 수 있어요.'}</p>
      {previewPending&&<p role="status" className="mt-2 text-xs text-blue-700">{draftInvalid?'일부 가림의 범위를 완성해 주세요.':'선택 중인 변경도 다른 항목이나 다음 단계로 이동할 때 반영됩니다.'}</p>}
    {pendingQuestions.length > 0 && <section aria-label="확인할 AI 질문" className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-blue-100 pt-3">
      <div><p className="text-sm font-semibold text-blue-800">추가 질문 {pendingQuestions.length}개 · 선택 사항</p><p className="mt-1 text-xs text-slate-600">문서 근거와 공유 상황만으로 판단하기 어려운 항목이에요. 미응답 항목은 전체 가림합니다.</p></div>
      <button type="button" disabled={disabled} onClick={() => {const i=pendingQuestions.findIndex(c=>c.id===selected?.id);void selectCandidate(pendingQuestions[(i+1)%pendingQuestions.length].id);}} className="min-h-11 border border-blue-300 bg-white px-4 text-sm font-medium text-blue-700 disabled:opacity-40">질문 확인하기</button>
    </section>}
    </div>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm"><p className="text-slate-600">{job.documentType?DOCTYPE_LABELS[job.documentType]:'문서 유형 미지정'}{job.documentTypeSource==='user'?' (직접 선택)':''}<span className="mx-2 text-slate-300">·</span>같은 정보도 위치별로 판단합니다</p><Link href="/context" className="text-xs text-blue-600">공유 목적·문서 유형 수정</Link></div>
    <div className="mb-4 flex flex-wrap items-center gap-3 text-xs">
      <button disabled={disabled||!job.aiEnabled} onClick={rerunAI} className="rounded-lg border border-slate-200 bg-white px-3 py-2 disabled:opacity-40">{analyzing?'AI 분석 중…':'AI 검토'}</button>
      <details className="flex-1"><summary className="cursor-pointer text-slate-500">AI 분석·제안 {job.suggestions.length?`(${job.suggestions.length})`:''}</summary><div className="mt-3"><AnalysisStatus job={job}/></div></details>
      <button disabled={disabled||!undo.length} onClick={undoLast} className="text-slate-500 disabled:opacity-30">되돌리기 ↶</button>
    </div>

    <div ref={documentWorkspaceRef} className="grid scroll-mt-44 items-start gap-5 min-[900px]:grid-cols-[minmax(0,1fr)_360px] xl:grid-cols-[minmax(0,1fr)_390px]">
      <DocumentView job={job} active={selected} previewCandidates={previewCandidates} previewPending={previewPending}
        viewMode={viewMode} onViewModeChange={setViewMode} onCandidateSelect={id=>void selectCandidate(id)}
        onSelect={hit=>void openManual(hit)}/>
      <aside ref={panelRef} aria-label={manualOpen?'정보 직접 추가':selected?'가림 방법 설정':'개인정보 목록'} className="min-w-0 scroll-mt-44 space-y-3 min-[900px]:sticky min-[900px]:top-44 min-[900px]:max-h-[calc(100dvh-12rem)] min-[900px]:overflow-y-auto min-[900px]:pr-1">
        {(selected||manualOpen)&&<div className="sticky top-0 z-10 border border-slate-200 bg-white px-4 py-3">
          <button type="button" disabled={!!disabled} onClick={()=>void selectCandidate(null)} className="min-h-9 text-sm font-medium text-blue-700 disabled:opacity-40">← {PII_LABELS[activeType]} 목록으로</button>
          {manualOpen&&<p className="mt-1 text-xs text-slate-500">추가할 정보를 원문에서 선택해 주세요.</p>}
        </div>}
        {!selected&&!manualOpen&&<section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <div className="flex justify-between px-4 py-3 text-sm font-semibold"><span>개인정보 유형</span><button disabled={disabled} onClick={()=>void openManual()} className="text-xs font-medium text-blue-600">직접 추가</button></div>
          <ReviewCategories candidates={candidates} activeType={activeType} selected={selected} visibleGroups={visible}
            onTypeChange={type=>void selectCandidate(null,type)} onSelect={id=>void selectCandidate(id)} disabled={!!disabled}/>
          <details className="border-t border-slate-200 px-4 py-3 text-xs"><summary className="cursor-pointer text-slate-500">이 유형에서 찾기·일괄 처리</summary><div className="mt-3 space-y-3">
            <input aria-label="개인정보 검색" placeholder="선택한 유형에서 검색" value={search} onChange={e=>setSearch(e.target.value)} className="w-full rounded-lg border border-slate-200 p-2"/>
            <select aria-label="표시할 정보" value={filter} onChange={e=>setFilter(e.target.value)} className="rounded border p-2"><option value="all">전체</option><option value="mask">가림</option><option value="keep">유지</option><option value="custom">직접 설정</option></select>
            <button disabled={disabled||!bulkItems.length} className="block text-blue-600 disabled:opacity-40" onClick={()=>{void save(bulkItems.map(c=>({id:c.id,method:'full',mask:[],confirmed:true}))).catch(e=>setError(String(e)));}}>표시된 {bulkItems.length}곳 전체 가림 적용</button>
          </div></details>
        </section>}
        {selected && !manualOpen && <>
          <CandidateEditor suggestion={selectedSuggestion} key={selected.id} candidate={selected} all={candidates} busy={!!disabled} onSave={save} onDone={()=>void selectCandidate(null)} keyboardMode={keyboardMode} onKeyboardModeChange={setKeyboardMode} onNavigate={navigate} onPreviewChange={updatePreview} onPreviewStart={startPreview}/>
          <details className="rounded-xl border border-slate-200 bg-white px-4 py-3">
            <summary className="cursor-pointer text-xs text-slate-600">문서에서 위치 확인 · {currentGroup.length}곳</summary>
            <div className="mt-3 flex flex-wrap gap-2">{currentGroup.map((c,i)=><button key={c.id} disabled={!!disabled} aria-label={`같은 정보 ${i+1}번째 위치`} aria-pressed={c.id===selected.id} onClick={()=>void selectCandidate(c.id)} className={`min-h-9 rounded-lg border px-2 py-1 text-xs disabled:opacity-40 ${!c.locationResolved?'border-amber-600 bg-amber-50 text-amber-800':c.id===selected.id?'border-blue-500 bg-blue-50 text-blue-700':'border-slate-200 bg-white'}`}>{i+1}번째{!c.locationResolved?' · 위치 연결':isUserDecision(c)?' · 직접 설정':''}</button>)}</div>
            {selected.locationContext&&<div className="mt-3 text-xs text-slate-500"><p>{selected.locationContext.section||'선택한 정보의 위치'}</p><p className="mt-1">{selected.locationContext.label}</p>{selected.linkedValue&&<p className="mt-1">줄바꿈된 정보의 일부 · {selected.linkedValue}</p>}</div>}
          </details>
        </>}
        {manualOpen&&<section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4"><div className="flex justify-between text-sm font-semibold"><span>{selected&&!selected.locationResolved?'원문 위치 연결':'직접 추가'}</span><button onClick={()=>setManualOpen(false)} className="text-xs text-slate-400">닫기</button></div><p className="text-xs text-slate-500">문서에서 글자를 드래그하거나 찾을 내용을 입력하세요.</p><input aria-label="원문에서 찾기" value={manualQuery} onChange={e=>{setManualQuery(e.target.value);setRange(null);}} placeholder="원문에서 찾기" className="w-full rounded-lg border border-slate-200 p-2 text-sm"/><select aria-label="추가할 정보 유형" value={manualType} onChange={e=>setManualType(e.target.value as PiiType)} className="w-full rounded-lg border p-2 text-sm">{Object.entries(PII_LABELS).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
          {selected&&!selected.locationResolved&&range?<button disabled={disabled} onClick={async()=>{try{await mutate('resolve',{candidateId:selected.id,...range});setManualOpen(false);setRange(null);}catch(e){setError(String(e));}}} className="w-full rounded-lg bg-blue-600 p-2 text-sm text-white">선택한 글자로 위치 연결</button>:<>
            {range&&<button disabled={disabled} onClick={()=>addHits([range])} className="w-full rounded-lg bg-blue-600 p-2 text-sm text-white">선택한 글자 추가 · {range.value}</button>}
            {!range&&<div className="max-h-44 overflow-auto">{hits.map((h,i)=><button key={`${h.unitId}-${h.start}`} onClick={()=>addHits([h])} disabled={disabled} className="flex w-full justify-between border-b border-slate-100 py-2 text-left text-xs"><span>{h.value} · {i+1}번째</span><span className="text-blue-600">{overlap(candidates,h)?'기존 정보 보기':'이 위치 추가'}</span></button>)}</div>}
            {hits.length>1&&<button disabled={disabled} onClick={()=>addHits(hits)} className="text-xs text-blue-600">찾은 {hits.length}곳 모두 추가</button>}
          </>}
        </section>}
        {selected&&!selected.locationResolved&&!manualOpen&&<button onClick={()=>void openManual()} className="w-full rounded-xl bg-amber-50 p-3 text-sm text-amber-800">원문에서 위치를 연결해 주세요 →</button>}
      </aside>
    </div>
    <p aria-live="polite" className="mt-3 min-h-5 text-xs text-slate-500">{notice}</p>
  </ServiceShell>;
}
