import {
  createJob,
  getJob,
  mutateJob,
  deleteJob,
  fetchPreview,
  fetchPage,
  downloadCopy,
  ServiceError,
  Job,
  Context,
} from './service';

export type Action = Parameters<typeof mutateJob>[2];
export type Variant = 'original' | 'copy';

export function isUpstageComplete(job: Pick<Job, 'aiEnabled' | 'analysis' | 'processingBlocked' | 'upstageReanalysisRequired'>): boolean {
  return job.aiEnabled === true && !job.analysis.incomplete && !job.processingBlocked && !job.upstageReanalysisRequired
    && ['parse', 'classify', 'extract'].every(stage => job.analysis[stage as 'parse'].status === 'completed')
    && ['completed', 'not_needed'].includes(job.analysis.hermes.status)
    && (job.analysis.hermes.required !== true || job.analysis.hermes.status === 'completed');
}

export type Snapshot = {
  job: Job | null;
  busy: boolean;
  error: string | null;
  restoring: boolean;
  tokenAvailable: boolean;
};

export class WorkspaceController {
  private snapshot: Snapshot = {
    job: null,
    busy: false,
    error: null,
    restoring: true,
    tokenAvailable: false,
  };

  private creds: { id: string; token: string } | null = null;
  private epoch: number = 0;
  private listeners: Set<() => void> = new Set();

  getSnapshot = () => this.snapshot;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  setError = (error: string | null) => {
    this.publish({ error });
  };

  private publish(patch: Partial<Snapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  private accept(job: Job, capturedEpoch: number) {
    if (capturedEpoch !== this.epoch) return;
    if (!this.creds || this.creds.id !== job.id) return;
    const current = this.snapshot.job;
    if (current !== null && job.version < current.version) return;
    this.publish({ job, tokenAvailable: true });
  }

  restore = async () => {
    if (typeof window === 'undefined') {
      this.publish({ restoring: false });
      return;
    }
    try {
      const raw = sessionStorage.getItem('garimi-job');
      if (!raw) {
        this.publish({ restoring: false });
        return;
      }
      const parsed = JSON.parse(raw) as { id: string; token: string } | null;
      if (
        !parsed ||
        typeof parsed.id !== 'string' ||
        typeof parsed.token !== 'string' ||
        parsed.id === '' ||
        parsed.token === ''
      ) {
        try { sessionStorage.removeItem('garimi-job'); } catch { /* ignore */ }
        this.publish({ restoring: false });
        return;
      }
      this.creds = { id: parsed.id, token: parsed.token };
      const capturedEpoch = ++this.epoch;
      try {
        const job = await getJob(parsed.id, parsed.token);
        this.accept(job, capturedEpoch);
      } catch (err) {
        const status = err instanceof ServiceError ? err.status : null;
        if (status === 401 || status === 403 || status === 404 || status === 410) {
          if (capturedEpoch === this.epoch) {
            this.creds = null;
            try { sessionStorage.removeItem('garimi-job'); } catch { /* ignore */ }
            this.publish({
              restoring: false,
              job: null,
              tokenAvailable: false,
              error: '작업이 만료되었거나 삭제되었습니다. 문서를 다시 선택해 주세요.',
            });
          }
          return;
        }
        // Unknown network error: publish Korean error, not silent
        if (capturedEpoch === this.epoch) {
          this.publish({
            restoring: false,
            error: '네트워크 오류가 발생했습니다. 다시 시도해 주세요.',
          });
        }
        throw err;
      }
      if (capturedEpoch === this.epoch) {
        this.publish({ restoring: false });
      }
    } catch {
      this.publish({ restoring: false });
    }
  };

  dispose = () => {
    this.epoch++;
  };

  start = async (file: File, context?: Context): Promise<Job> => {
    if (this.snapshot.busy) throw new Error('작업 중입니다.');
    if (this.creds) {
      throw new Error('기존 작업을 삭제한 뒤 새 문서를 선택해 주세요.');
    }
    this.publish({ busy: true, error: null });
    const capturedEpoch = ++this.epoch;
    try {
      const result = await createJob(file, true, context);
      if (capturedEpoch !== this.epoch) {
        throw new Error('cancelled');
      }
      this.creds = { id: result.job.id, token: result.token };
      try {
        if (typeof window !== 'undefined') {
          sessionStorage.setItem(
            'garimi-job',
            JSON.stringify({ id: this.creds.id, token: this.creds.token })
          );
        }
      } catch { /* storage 오류 무시 */ }
      this.accept(result.job, capturedEpoch);
      return result.job;
    } catch (err) {
      this.setError(err instanceof ServiceError ? err.message : String(err));
      throw err;
    } finally {
      if (capturedEpoch === this.epoch) {
        this.publish({ busy: false });
      }
    }
  };

  refresh = async (): Promise<Job | null> => {
    if (!this.creds) return null;
    if (this.snapshot.busy) return this.snapshot.job;
    const capturedEpoch = this.epoch;
    const capturedCreds = this.creds;
    try {
      const job = await getJob(capturedCreds.id, capturedCreds.token);
      this.accept(job, capturedEpoch);
      return job;
    } catch (err) {
      const status = err instanceof ServiceError ? err.status : null;
      if (status === 401 || status === 403 || status === 404 || status === 410) {
        if (capturedEpoch === this.epoch && capturedCreds.id === this.creds?.id) {
          this.creds = null;
          try { sessionStorage.removeItem('garimi-job'); } catch { /* ignore */ }
          this.publish({
            job: null,
            tokenAvailable: false,
            error: '작업이 만료되었거나 삭제되었습니다. 문서를 다시 선택해 주세요.',
          });
        }
        return null;
      }
      if (status === 409) throw err;
      if (capturedEpoch === this.epoch) {
        this.publish({ error: err instanceof ServiceError ? err.message : String(err) });
      }
      throw err;
    }
  };

  mutate = async (action: Action, payload: Record<string, unknown>): Promise<Job> => {
    if (this.snapshot.busy) throw new Error('작업 중입니다.');
    if (!this.creds || !this.snapshot.job) throw new Error('작업 정보가 없습니다.');
    if (!this.snapshot.job.aiEnabled || this.snapshot.job.upstageReanalysisRequired) throw new Error('이전 작업은 자동 전송하지 않습니다. 문서를 새로 올려 Upstage 분석을 시작해 주세요.');
    if (['render', 'ack', 'auto-export', 'ai-review', 'prepare-export'].includes(action) && !isUpstageComplete(this.snapshot.job)) {
      throw new Error('Upstage 분석을 완료한 뒤 내보낼 수 있습니다. 가림 검토에서 AI 검토를 다시 실행해 주세요.');
    }
    if (action === 'auto-export' && payload.mode !== 'ai_automatic') throw new Error('AI 추천 자동 적용만 사용할 수 있습니다.');
    const currentVersion = this.snapshot.job.version;
    const capturedEpoch = ++this.epoch;
    this.publish({ busy: true, error: null });
    try {
      const result = await mutateJob(
        this.creds.id,
        this.creds.token,
        action,
        { ...payload, version: currentVersion }
      );
      this.accept(result, capturedEpoch);
      return result;
    } catch (err) {
      const status = err instanceof ServiceError ? err.status : null;
      if (status === 409) {
        this.publish({ busy: false });
        try {
          await this.refresh();
        } catch {
          // catch refresh error but rethrow original 409
        }
        throw err;
      }
      this.setError(err instanceof ServiceError ? err.message : String(err));
      throw err;
    } finally {
      if (capturedEpoch === this.epoch) {
        this.publish({ busy: false });
      }
    }
  };

  erase = async (): Promise<void> => {
    if (this.snapshot.busy) throw new Error('작업 중입니다.');
    if (!this.creds) return;
    this.publish({ busy: true, error: null });
    const capturedEpoch = this.epoch;
    try {
      await deleteJob(this.creds.id, this.creds.token);
      this.epoch++;
      this.creds = null;
      try { sessionStorage.removeItem('garimi-job'); } catch { /* ignore */ }
      this.publish({ job: null, tokenAvailable: false, error: null, busy: false });
    } catch (err) {
      const status = err instanceof ServiceError ? err.status : null;
      if (status === 404 || status === 410) {
        this.epoch++;
        this.creds = null;
        try { sessionStorage.removeItem('garimi-job'); } catch { /* ignore */ }
        this.publish({ job: null, tokenAvailable: false, error: null, busy: false });
        return;
      }
      if (status === 403) {
        this.setError(err instanceof ServiceError ? err.message : String(err));
        throw err;
      }
      this.setError(err instanceof ServiceError ? err.message : String(err));
      throw err;
    } finally {
      if (capturedEpoch === this.epoch) {
        this.publish({ busy: false });
      }
    }
  };

  preview = async (variant: Variant) => {
    if (!this.creds) throw new Error('작업 정보가 없습니다.');
    return fetchPreview(this.creds.id, this.creds.token, variant);
  };

  page = async (number: number, variant: Variant) => {
    if (!this.creds) throw new Error('작업 정보가 없습니다.');
    return fetchPage(this.creds.id, this.creds.token, number, variant);
  };

  download = async (filename: string) => {
    if (!this.creds) throw new Error('작업 정보가 없습니다.');
    if (!this.snapshot.job || !isUpstageComplete(this.snapshot.job)) throw new Error('Upstage 분석이 완료되지 않아 다운로드할 수 없습니다.');
    return downloadCopy(this.creds.id, this.creds.token, filename);
  };
}
