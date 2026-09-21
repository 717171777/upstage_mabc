// src/lib/service.ts
// Typed browser client + data types. No React, no mocks, no external API keys.
// All requests same-origin '/api/service'. Backend field names are exact.

export type PiiType =
  | 'name' | 'phone' | 'email' | 'address' | 'dob'
  | 'resident_id' | 'foreign_id' | 'passport' | 'driver_license'
  | 'account' | 'card' | 'management_id';

export type DocumentType =
  | 'report_minutes' | 'contract_agreement' | 'transaction_settlement'
  | 'personnel_roster' | 'case_record' | 'other';

export type Method = 'full' | 'partial' | 'delete' | 'keep';

export interface Candidate {
  id: string;
  type: PiiType;
  value: string;
  unitId: string | null;
  start: number | null;
  end: number | null;
  page: number | null;
  method: Method;
  mask: [number, number][];
  confirmed: boolean;
  decisionSource?: 'user' | 'ai_default' | 'ai_automatic' | 'local_automatic';
  locationResolved: boolean;
  source?: string;
  reason?: string;
  alternativeTypes?: string[];
  context_raw?: string;
  role_raw?: string;
  extractedValue?: string;
  locationContext?: {label:string;region:string;section?:string;page?:number;table?:{index:number;row:number;column:number};evidence:{unitId:string;text:string;start:number;end:number;relation:string}[]};
  linkedValue?: string;
}

export interface Locator {
  part?: string;
  p_index?: number;
}

export interface CharInfo {
  c: string;
  bbox: number[];
}

export interface Unit {
  id: string;
  text: string;
  page?: number | null;
  locator?: Locator;
  chars?: CharInfo[];
}

export interface Metadata {
  id: string;
  label: string;
  value: string;
  source: string;
  action: 'delete' | 'keep';
}

export interface AnalysisStage {
  status: string;
  model?: string | null;
  required?: boolean;
  errorCode?: string | null;
  totalBatches?: number;
  completedBatches?: number;
  currentBatch?: number;
  attempt?: number;
  maxAttempts?: number;
}

export interface Analysis {
  parse: AnalysisStage;
  classify: AnalysisStage;
  extract: AnalysisStage;
  hermes: AnalysisStage;
  warnings: string[];
  incomplete?: boolean;
}

export interface Check {
  name: string;
  passed: boolean;
  detail?: string;
}

export interface Artifact {
  sha256: string;
  version: number;
  validationVersion: number;
  checks: Check[];
}

export interface Context {
  recipient: string | null;
  purpose: string;
  keepInfo: string | null;
}

export interface Runtime {
  platform: string;
  execution: string;
  model: string;
  llmUsed: boolean;
}

export interface Suggestion {
  question?: string;
  id?: string;
  candidateId: string;
  type: string;
  content: string;
  reason?: string;
  recommendation?: 'full' | 'partial' | 'keep';
  presetId?: string;
  evidence?: string;
  source?: 'local_rules' | 'sp4';
}

export interface Job {
  fullRedaction?: boolean;
  analysisResume?: { completedStages: string[]; savedBatches: number };
  id: string;
  version: number;
  status: string;
  fileName: string;
  format: 'pdf' | 'docx';
  aiEnabled: boolean;
  upstageRequired?: boolean;
  processingBlocked?: string | null;
  upstageReanalysisRequired?: boolean;
  expiresAt: string;
  units: Unit[];
  metadata: Metadata[];
  uninspected: string[];
  candidates: Candidate[];
  documentType: DocumentType | null;
  suggestions: Suggestion[];
  analysis: Analysis;
  artifact: Artifact | null;
  acknowledged: boolean;
  metadataReviewed: boolean;
  context: Context;
  runtime?: Runtime;
  acknowledgmentMode?: 'ai_automatic' | 'local_automatic' | 'manual';
  documentTypeSource?: 'local_rules' | 'upstage' | 'user';
  localCoverage?: string;
  autoExport?: {full: number; partial: number; keep: number; manualPreserved: number; version: number; sha256: string};
  aiReview?: {status: 'running' | 'completed' | 'partial' | 'failed'; version: number; sha256: string;
    findings: {id: string; type: PiiType; value: string; reason: string; plannedKeep: boolean; locationResolved: boolean}[];
    warnings: string[]; stages?: Record<string, AnalysisStage>};
}

export class ServiceError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = 'ServiceError';
  }
}

const BASE = '/api/service';

async function requestJSON<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const headers = new Headers(options.headers as Record<string, string> | undefined);
  headers.set('Cache-Control', 'no-store');

  if (options.body !== undefined && typeof options.body === 'string') {
    headers.set('Content-Type', 'application/json');
  }

  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const fetchOptions: RequestInit = {
    ...options,
    headers,
    cache: 'no-store',
  };
  if (options.signal) {
    fetchOptions.signal = options.signal;
  }

  const res = await fetch(BASE + path, fetchOptions);

  let body: { error?: string; code?: string; message?: string } | null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) {
    const msg = body?.error || body?.message || '요청 처리 중 오류가 발생했습니다.';
    const code = body?.code || 'UNKNOWN';
    throw new ServiceError(res.status, code, msg);
  }

  if (body === null) {
    throw new ServiceError(res.status, 'INVALID_JSON', '응답 형식이 올바르지 않습니다.');
  }

  return body as T;
}

export async function createJob(file: File, aiEnabled: boolean, context?: Context): Promise<{ job: Job; token: string }> {
  if (!file || file.size === 0) {
    throw new ServiceError(400, 'EMPTY_FILE', '파일이 비어 있습니다.');
  }
  if (file.size > 10 * 1024 * 1024) {
    throw new ServiceError(413, 'FILE_TOO_LARGE', '파일 크기는 10MB 이하여야 합니다.');
  }
  const ext = file.name.split('.').pop()?.toLowerCase();
  if (ext !== 'pdf' && ext !== 'docx') {
    throw new ServiceError(400, 'INVALID_FORMAT', '지원하는 형식은 PDF와 DOCX입니다.');
  }

  const form = new FormData();
  form.append('file', file);
  form.append('aiEnabled', String(aiEnabled));
  if (context) form.append('context', JSON.stringify(context));

  return requestJSON<{ job: Job; token: string }>('/jobs', { method: 'POST', body: form });
}

export async function getJob(id: string, token: string): Promise<Job> {
  return requestJSON<Job>(`/jobs/${id}`, { method: 'GET' }, token);
}

export async function mutateJob(
  id: string,
  token: string,
  action: 'plan' | 'manual' | 'resolve' | 'context' | 'render' | 'ack' | 'auto-export' | 'ai-review' | 'retry-analysis',
  payload: Record<string, unknown>,
): Promise<Job> {
  const method = action === 'plan' ? 'PATCH' : 'POST';
  return requestJSON<Job>(
    `/jobs/${id}/${action}`,
    { method, body: JSON.stringify(payload) },
    token,
  );
}

export async function deleteJob(id: string, token: string): Promise<void> {
  await requestJSON<void>(`/jobs/${id}`, { method: 'DELETE' }, token);
}

export type DocumentBlock = {kind: 'paragraph'; unitId: string; heading?: boolean; align?: 'left' | 'center' | 'right'; region?: string}
  | {kind: 'table'; rows: {colSpan: number; blocks: DocumentBlock[]}[][]; region?: string};

export interface PreviewResult {
  blocks?: DocumentBlock[];
  format: 'pdf' | 'docx';
  units: Unit[];
  metadata: Metadata[];
  uninspected: string[];
  pageSizes?: Record<string, { width: number; height: number }>;
}

export async function fetchPreview(
  id: string,
  token: string,
  variant: 'original' | 'copy',
): Promise<PreviewResult> {
  const params = new URLSearchParams({ variant });
  return requestJSON<PreviewResult>(`/jobs/${id}/preview?${params}`, { method: 'GET' }, token);
}

export async function fetchPage(
  id: string,
  token: string,
  page: number,
  variant: 'original' | 'copy',
): Promise<Blob> {
  const params = new URLSearchParams({ variant });
  const res = await fetch(`${BASE}/jobs/${id}/page/${page}?${params}`, {
    method: 'GET',
    cache: 'no-store',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    let msg = '페이지를 불러오지 못했습니다.';
    let code = 'UNKNOWN';
    try {
      const b = await res.json();
      msg = b.error || b.message || msg;
      code = b.code || code;
    } catch {}
    throw new ServiceError(res.status, code, msg);
  }
  return res.blob();
}

export async function downloadCopy(
  id: string,
  token: string,
  filename?: string,
): Promise<Blob> {
  let url = `${BASE}/jobs/${id}/download`;
  if (filename !== undefined) {
    const params = new URLSearchParams({ filename });
    url += `?${params}`;
  }
  const res = await fetch(url, {
    method: 'GET',
    cache: 'no-store',
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    let msg = '다운로드에 실패했습니다.';
    let code = 'UNKNOWN';
    try {
      const b = await res.json();
      msg = b.error || b.message || msg;
      code = b.code || code;
    } catch {}
    throw new ServiceError(res.status, code, msg);
  }
  return res.blob();
}

export async function exampleFile(format: 'pdf' | 'docx'): Promise<File> {
  const res = await fetch(`${BASE}/examples?format=${format}`, { cache: 'no-store' });
  if (!res.ok) {
    throw new ServiceError(res.status, 'EXAMPLE_UNAVAILABLE', '예시 파일을 불러오지 못했습니다.');
  }
  const blob = await res.blob();
  return new File([blob], `합성예시.${format}`, { type: blob.type || 'application/octet-stream' });
}

export interface HealthResult {
  ok: boolean;
  aiConfigured: boolean;
  model: string | null;
  syntheticOnly: boolean;
  upstageRequired: boolean;
}

export async function fetchHealth(): Promise<HealthResult> {
  return requestJSON<HealthResult>('/health', { method: 'GET' });
}

// Korean label maps
export const PII_LABELS: Record<PiiType, string> = {
  name: '이름',
  phone: '전화번호',
  email: '이메일',
  address: '주소',
  dob: '생년월일',
  resident_id: '주민등록번호',
  foreign_id: '외국인등록번호',
  passport: '여권번호',
  driver_license: '운전면허번호',
  account: '계좌번호',
  card: '카드번호',
  management_id: '관리번호',
};

export const DOCTYPE_LABELS: Record<DocumentType, string> = {
  report_minutes: '결과보고서·회의록',
  contract_agreement: '계약서·합의서',
  transaction_settlement: '거래정산서',
  personnel_roster: '인사명부',
  case_record: '사건기록',
  other: '기타',
};

export const METHOD_LABELS: Record<Method, string> = {
  full: '전체 마스킹',
  partial: '부분 마스킹',
  delete: '삭제',
  keep: '유지',
};
