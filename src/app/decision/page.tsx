'use client';

import {useApp} from '@/contexts/AppContext';
import {ServiceShell, AnalysisStatus, EmptyJob, LegacyJobNotice} from '@/components/service-shell';
import {DocumentView} from '@/components/document-view';
import {CandidateEditor} from '@/components/candidate-editor';
import {Job, Candidate, Method, PiiType, PII_LABELS, DOCTYPE_LABELS, METHOD_LABELS} from '@/lib/service';
import {useState, useRef, useEffect, useMemo, useCallback} from 'react';
import {useRouter} from 'next/navigation';
import {repeatedReviewOrder} from '@/lib/repeat-decisions';
import {isUpstageComplete} from '@/lib/workspace';
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
  const {job,busy,mutate,download,setError}=useApp();
  const router=useRouter();
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [filter,setFilter]=useState('all');
  const [search,setSearch]=useState('');
  const [sort,setSort]=useState('doc');
  const [manualOpen,setManualOpen]=useState(false);
  const [manualQuery,setManualQuery]=useState('');
  const [manualType,setManualType]=useState<PiiType>('name');
  const [range,setRange]=useState<Hit|null>(null);
  const [undo,setUndo]=useState<Decision[][]>([]);
  const [working,setWorking]=useState(false);
  const [notice,setNotice]=useState('');
  const [reviewAttempt,setReviewAttempt]=useState<string|null>(null);
  const showReviewNeeded=reviewAttempt===job?.id;
  const lock=useRef(false);
  const candidates=useMemo(()=>job?.candidates??[],[job?.candidates]);
  const selected=candidates.find(c=>c.id===selectedId)??null;
  const selectedSuggestion=job?.suggestions.find(s=>s.candidateId===selected?.id&&s.question)??job?.suggestions.find(s=>s.candidateId===selected?.id);
  const pendingQuestions=candidates.filter(c=>!c.confirmed && job?.suggestions.some(s=>s.candidateId===c.id && s.question));
  const initialized=useRef<string|null>(null);
  const analyzing=job?.status==='analyzing'||job?.status==='rendering';
  const disabled=busy||working||analyzing;
  useEffect(()=>{if(job && initialized.current!==job.id){initialized.current=job.id;setSelectedId(job.candidates[0]?.id??null);setUndo([]);}},[job]);

  const groups=useMemo(()=>{
    const map=new Map<string,Candidate[]>();
    for(const c of candidates)map.set(groupKey(c),[...(map.get(groupKey(c))??[]),c]);
    return [...map.values()];
  },[candidates]);
  const navigationCandidates=useMemo(()=>{
    const unitOrder=new Map(job?.units.map((unit,index)=>[unit.id,index]));
    return [...candidates].sort((a,b)=>(unitOrder.get(a.unitId??'')??Infinity)-(unitOrder.get(b.unitId??'')??Infinity)||(a.start??Infinity)-(b.start??Infinity));
  },[candidates,job?.units]);
  const matchesFilter=useCallback((c:Candidate)=>(filter==='all'||(filter==='unconfirmed'&&!c.confirmed)||(filter==='keep'&&c.method==='keep')||(filter==='mask'&&c.method!=='keep')) && (!search || `${c.value} ${PII_LABELS[c.type]}`.toLowerCase().includes(search.toLowerCase())),[filter,search]);
  const visible=useMemo(()=>{
    const list=groups.filter(g=>g.some(matchesFilter));
    return sort==='type'?[...list].sort((a,b)=>a[0].type.localeCompare(b[0].type)):list;
  },[groups,matchesFilter,sort]);
  const bulkItems=visible.flat().filter(c=>c.locationResolved && matchesFilter(c));
  const currentGroup=selected?groups.find(g=>groupKey(g[0])===groupKey(selected))??[]:[];
  const confirmed=candidates.filter(c=>c.confirmed).length;
  const unresolved=candidates.filter(c=>!c.locationResolved).length;
  const canExport=!!job && isUpstageComplete(job) && confirmed===candidates.length && !unresolved && !job.analysis.incomplete && !(job.fullRedaction && job.uninspected.length) && !disabled;
  const failedAI=job&&(['parse','classify','extract','hermes'] as const).some(k=>job.analysis[k].status==='failed');
  const quickReason=!job?.aiEnabled?'문서를 새로 올려 Upstage 분석을 시작해 주세요.':failedAI?'Upstage 분석 일부가 실패했습니다. AI 검토로 다시 시도해 주세요.':!isUpstageComplete(job)?'Upstage 분석을 완료해야 파일을 받을 수 있습니다.':unresolved?`원문 위치 ${unresolved}곳을 연결하면 바로 받을 수 있습니다.`:job.uninspected.length?'검사하지 못한 영역이 있어 직접 확인이 필요합니다.':'';
  const hits=job?findOccurrences(job,manualQuery):[];

  const save=useCallback(async(decisions:Decision[])=>{
    if(busy||lock.current||!job)throw new Error('저장이 끝난 뒤 다시 시도해 주세요.');
    lock.current=true;setWorking(true);
    const snapshot=job.candidates.filter(c=>decisions.some(d=>d.id===c.id)).map(c=>({id:c.id,method:c.method,mask:c.mask,confirmed:c.confirmed}));
    try{const updated=await mutate('plan',{candidates:decisions});setUndo(stack=>[...stack,snapshot].slice(-20));return updated;}
    finally{lock.current=false;setWorking(false);}
  },[busy,job,mutate]);
  const navigate=(kind:'occurrence'|'group',reverse:boolean)=>{
    if(!selected)return;
    const step=reverse?-1:1;
    if(kind==='occurrence'){
      const sameType=navigationCandidates.filter(c=>c.type===selected.type);
      const index=sameType.findIndex(c=>c.id===selected.id);
      setSelectedId(sameType[(index+step+sameType.length)%sameType.length].id);
      setNotice(sameType.length===1?'이 종류의 정보는 문서 안에 한 곳만 있습니다.':`${PII_LABELS[selected.type]}의 ${reverse?'이전':'다음'} 정보로 이동했습니다.`);
    }else{
      const ordered=repeatedReviewOrder(navigationCandidates);
      const index=ordered.findIndex(c=>c.id===selected.id);
      const next=ordered[(index+step+ordered.length)%ordered.length];
      setSelectedId(next.id);
      const same=ordered.filter(c=>c.type===next.type && c.value===next.value);
      setNotice(`${PII_LABELS[next.type]} · 같은 값 ${same.findIndex(c=>c.id===next.id)+1}/${same.length}번째 위치`);

    }
  };
  const undoLast=useCallback(async()=>{
    if(disabled||job?.fullRedaction||!undo.length)return;
    try{await mutate('plan',{candidates:undo[undo.length-1].filter(d=>candidates.some(c=>c.id===d.id))});setUndo(s=>s.slice(0,-1));setSelectedId(null);setNotice('직전 선택을 되돌렸습니다.');}catch(e){setError(e instanceof Error?e.message:String(e));}
  },[disabled,job?.fullRedaction,undo,mutate,candidates,setError]);
  useEffect(()=>{
    const key=(e:KeyboardEvent)=>{if(e.defaultPrevented || (e.target as HTMLElement).closest('input,textarea,select,[contenteditable="true"]'))return;if((e.metaKey||e.ctrlKey)&&e.key==='z'){e.preventDefault();void undoLast();}};
    window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);
  },[undoLast]);
  const addHits=async(items:Hit[])=>{
    if(disabled||lock.current||!job)return;lock.current=true;setWorking(true);
    try{
      let current=job;
      for(const hit of items){const existing=overlap(current.candidates,hit);if(existing){setSelectedId(existing.id);continue;}
        current=await mutate('manual',{type:manualType,...hit});const added=current.candidates.find(c=>c.unitId===hit.unitId&&c.start===hit.start&&c.end===hit.end);if(added)setSelectedId(added.id);
      }
      setRange(null);setManualOpen(false);
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{lock.current=false;setWorking(false);}
  };
  const quickDownload=async()=>{
    if(disabled||quickReason||lock.current)return;lock.current=true;setWorking(true);setError(null);
    try{
      const result=await mutate('auto-export',{mode:'ai_automatic'});
      if(result.status!=='validated'||!result.artifact||!result.acknowledged)throw new Error('저장 사본 검사가 완료되지 않았습니다.');
      const name=result.fileName.replace(/\.[^.]+$/,'')+'-공유본.'+result.format;
      const blob=await download(name),url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1500);
      router.push('/export?downloaded=1');
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{lock.current=false;setWorking(false);}
  };
  const requestExport=()=>{
    if(disabled)return;
    if(canExport){router.push('/export');return;}
    setReviewAttempt(job?.id??null);setFilter('all');setSearch('');setManualOpen(false);
    const first=navigationCandidates.find(c=>!c.confirmed||!c.locationResolved);
    if(first){
      setSelectedId(first.id);
      setNotice(`검토가 필요한 ${candidates.filter(c=>!c.confirmed||!c.locationResolved).length}곳을 빨간색으로 표시했습니다.`);
      requestAnimationFrame(()=>{
        const item=document.querySelector<HTMLElement>('[data-review-needed="true"]');
        const panel=item?.closest('aside');
        if(panel)panel.scrollTop=0;
        item?.scrollIntoView({block:'center',behavior:'instant'});
      });
    }else setNotice(quickReason||'문서 분석을 완료한 뒤 내보낼 수 있습니다.');
  };
  const rerunAI=async()=>{if(!job||disabled||!job.aiEnabled)return;try{await mutate('retry-analysis',{});setNotice('저장된 결과를 재사용하고 남은 분석을 진행합니다.');}catch(e){setError(e instanceof Error?e.message:String(e));}};
  if(!job)return <EmptyJob/>;
  if(!job.aiEnabled || job.upstageReanalysisRequired)return <LegacyJobNotice/>;

  return <ServiceShell step={2} title="가림 검토">
    <div className="sticky top-0 z-20 -mx-1 mb-5 rounded-2xl border border-slate-200 bg-white/95 p-4 backdrop-blur">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><p className="font-semibold text-slate-800">{confirmed} / {candidates.length}곳 확인</p><p className="mt-1 text-xs text-slate-500">원문에서 정보를 누르고 가림 방법을 고르세요.</p></div>
        <div className="flex flex-wrap gap-2"><button onClick={quickDownload} disabled={disabled||!!quickReason} className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm font-medium text-blue-700 disabled:opacity-40">{working?'처리 중…':job.fullRedaction?'전체 가림으로 바로 받기':'기본 가림으로 바로 받기'}</button>
          <button onClick={requestExport} disabled={disabled} className="rounded-xl bg-blue-600 px-5 py-3 text-sm font-semibold text-white disabled:opacity-40">내보내기 →</button>
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-500">{quickReason||(job.fullRedaction?'찾은 개인정보를 모두 전체 가림합니다. 유지·부분 가림과 AI 추천보다 우선합니다.':'AI가 가림·유지를 판단하고, 제출 상황에 따라 부분 가림을 추천할 수 있습니다. 직접 확정한 방법은 보존됩니다.')}</p>
      {!canExport && !disabled && <p className="mt-1 text-xs text-slate-500">직접 내보내기: {candidates.length-confirmed}곳 미확인{unresolved?` · ${unresolved}곳 위치 확인 필요`:''}</p>}
    {pendingQuestions.length > 0 && <section aria-label="확인할 AI 질문" className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-blue-100 pt-3">
      <div><p className="text-sm font-semibold text-blue-800">AI 확인 질문 {pendingQuestions.length}개</p><p className="mt-1 text-xs text-slate-600">문서 근거와 공유 상황만으로 판단하기 어려운 항목이에요. 미응답 항목은 전체 가림합니다.</p></div>
      <button type="button" disabled={disabled} onClick={() => {const i=pendingQuestions.findIndex(c=>c.id===selected?.id);setSelectedId(pendingQuestions[(i+1)%pendingQuestions.length].id);setManualOpen(false);}} className="min-h-11 border border-blue-300 bg-white px-4 text-sm font-medium text-blue-700 disabled:opacity-40">질문 확인하기</button>
    </section>}
    </div>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm"><p className="text-slate-600">{job.documentType?DOCTYPE_LABELS[job.documentType]:'문서 유형 미지정'}{job.documentTypeSource==='user'?' (직접 선택)':''}<span className="mx-2 text-slate-300">·</span>같은 정보도 위치별로 판단합니다</p><Link href="/context" className="text-xs text-blue-600">공유 목적·문서 유형 수정</Link></div>
    <div className="mb-4 flex flex-wrap items-center gap-3 text-xs">
      <button disabled={disabled||!job.aiEnabled} onClick={rerunAI} className="rounded-lg border border-slate-200 bg-white px-3 py-2 disabled:opacity-40">{analyzing?'AI 분석 중…':'AI 검토'}</button>
      <details className="flex-1"><summary className="cursor-pointer text-slate-500">AI 분석·제안 {job.suggestions.length?`(${job.suggestions.length})`:''}</summary><div className="mt-3"><AnalysisStatus job={job}/></div></details>
      <button disabled={disabled||job.fullRedaction||!undo.length} onClick={undoLast} className="text-slate-500 disabled:opacity-30">되돌리기 ↶</button>
    </div>

    <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_390px]">
      <DocumentView job={job} active={selected} onCandidateSelect={id=>{setSelectedId(id);setManualOpen(false);}}
        onSelect={hit=>{setRange(hit);setManualQuery(hit.value);setManualOpen(true);}}/>
      <aside className="min-w-0 space-y-3 lg:sticky lg:top-36 lg:max-h-[calc(100vh-10rem)] lg:overflow-y-auto lg:pr-1">
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <div className="flex justify-between px-4 py-3 text-sm font-semibold"><span>찾은 개인정보 <span className="font-normal text-slate-400">{groups.length}개</span></span><button onClick={()=>setManualOpen(v=>!v)} className="text-xs font-medium text-blue-600">직접 추가</button></div>
          <div className="max-h-44 overflow-auto border-t border-slate-100">{visible.map(g=>{const pending=g.filter(c=>!c.confirmed||!c.locationResolved).length;const needsReview=showReviewNeeded&&pending>0;return <button data-review-needed={needsReview||undefined} key={groupKey(g[0])} onClick={()=>{setSelectedId((g.find(c=>!c.confirmed||!c.locationResolved)??g[0]).id);setManualOpen(false);}} className={`flex w-full items-center justify-between gap-2 border-b border-slate-50 px-4 py-2.5 text-left text-sm ${needsReview?'bg-red-50 text-red-800 border-l-2 border-l-red-600':selected&&groupKey(selected)===groupKey(g[0])?'bg-blue-50':'hover:bg-slate-50'}`}><span className="truncate"><span className="mr-2 text-xs text-slate-400">{PII_LABELS[g[0].type]}</span>{g[0].value}</span><span className={`shrink-0 text-right text-xs ${needsReview?'text-red-700':'text-slate-500'}`}>{needsReview&&<span className="block font-semibold">검토 필요 {pending}곳</span>}<span className="block">{g.length}곳{g.every(c=>c.confirmed)?' · 확인됨':''}</span><span className="text-[11px] text-slate-400">{new Set(g.map(c=>c.method)).size>1?'위치마다 다름':METHOD_LABELS[g[0].method]}</span></span></button>})}{!visible.length&&<p className="p-4 text-sm text-slate-400">조건에 맞는 정보가 없습니다.</p>}</div>
          <details className="px-4 py-3 text-xs"><summary className="cursor-pointer text-slate-500">찾기·일괄 처리</summary><div className="mt-3 space-y-3"><input aria-label="개인정보 검색" placeholder="정보 검색" value={search} onChange={e=>setSearch(e.target.value)} className="w-full rounded-lg border border-slate-200 p-2"/><div className="flex gap-2"><select aria-label="표시할 정보" value={filter} onChange={e=>setFilter(e.target.value)} className="rounded border p-2"><option value="all">전체</option><option value="unconfirmed">미확인</option><option value="mask">가림</option><option value="keep">유지</option></select><select aria-label="정렬" value={sort} onChange={e=>setSort(e.target.value)} className="rounded border p-2"><option value="doc">문서순</option><option value="type">유형별</option></select></div><button disabled={disabled||!bulkItems.length} className="text-blue-600 disabled:opacity-40" onClick={()=>{void save(bulkItems.map(c=>({id:c.id,method:'full',mask:[],confirmed:true}))).catch(e=>setError(String(e)));}}>표시된 {bulkItems.length}곳 전체 가림으로 확인</button></div></details>
        </section>
        {selected && !manualOpen && <><div className="flex flex-wrap items-center gap-2 px-1 text-xs"><span className="text-slate-500">같은 정보의 위치</span>{currentGroup.map((c,i)=><button key={c.id} aria-label={`같은 정보 ${i+1}번째 위치`} aria-pressed={c.id===selected.id} onClick={()=>setSelectedId(c.id)} className={`min-w-8 rounded-lg border px-2 py-1.5 ${showReviewNeeded&&(!c.confirmed||!c.locationResolved)?'border-red-600 bg-red-50 text-red-700':c.id===selected.id?'border-blue-500 bg-blue-50 text-blue-700':'border-slate-200 bg-white'}`}>{i+1}{showReviewNeeded&&(!c.confirmed||!c.locationResolved)?' · 검토 필요':c.confirmed?' · 확인됨':''}</button>)}</div>
          {selected.locationContext&&<div className="rounded-xl bg-slate-100 px-3 py-2 text-xs text-slate-600"><p className="font-medium">{selected.locationContext.section||'선택한 정보의 위치'}</p><p className="mt-1">{selected.locationContext.label}</p>{selected.linkedValue&&<p className="mt-1">줄바꿈된 정보의 일부 · {selected.linkedValue}</p>}</div>}
          <CandidateEditor suggestion={selectedSuggestion} key={`${selected.id}:${job.fullRedaction}`} candidate={selected} all={candidates} busy={!!disabled || job.fullRedaction === true} onSave={save} onClose={()=>setSelectedId(null)} onNavigate={navigate}/>

        </>}
        {manualOpen&&<section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4"><div className="flex justify-between text-sm font-semibold"><span>{selected&&!selected.locationResolved?'원문 위치 연결':'직접 추가'}</span><button onClick={()=>setManualOpen(false)} className="text-xs text-slate-400">닫기</button></div><p className="text-xs text-slate-500">문서에서 글자를 드래그하거나 찾을 내용을 입력하세요.</p><input aria-label="원문에서 찾기" value={manualQuery} onChange={e=>{setManualQuery(e.target.value);setRange(null);}} placeholder="원문에서 찾기" className="w-full rounded-lg border border-slate-200 p-2 text-sm"/><select aria-label="추가할 정보 유형" value={manualType} onChange={e=>setManualType(e.target.value as PiiType)} className="w-full rounded-lg border p-2 text-sm">{Object.entries(PII_LABELS).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
          {selected&&!selected.locationResolved&&range?<button disabled={disabled} onClick={async()=>{try{await mutate('resolve',{candidateId:selected.id,...range});setManualOpen(false);setRange(null);}catch(e){setError(String(e));}}} className="w-full rounded-lg bg-blue-600 p-2 text-sm text-white">선택한 글자로 위치 연결</button>:<>
            {range&&<button disabled={disabled} onClick={()=>addHits([range])} className="w-full rounded-lg bg-blue-600 p-2 text-sm text-white">선택한 글자 추가 · {range.value}</button>}
            {!range&&<div className="max-h-44 overflow-auto">{hits.map((h,i)=><button key={`${h.unitId}-${h.start}`} onClick={()=>addHits([h])} disabled={disabled} className="flex w-full justify-between border-b border-slate-100 py-2 text-left text-xs"><span>{h.value} · {i+1}번째</span><span className="text-blue-600">{overlap(candidates,h)?'기존 정보 보기':'이 위치 추가'}</span></button>)}</div>}
            {hits.length>1&&<button disabled={disabled} onClick={()=>addHits(hits)} className="text-xs text-blue-600">찾은 {hits.length}곳 모두 추가</button>}
          </>}
        </section>}
        {selected&&!selected.locationResolved&&!manualOpen&&<button onClick={()=>{setManualOpen(true);setManualQuery(selected.value);}} className="w-full rounded-xl bg-amber-50 p-3 text-sm text-amber-800">원문에서 위치를 연결해 주세요 →</button>}
      </aside>
    </div>
    <p aria-live="polite" className="mt-3 min-h-5 text-xs text-slate-500">{notice}</p>
  </ServiceShell>;
}
