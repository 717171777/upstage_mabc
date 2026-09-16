'use client';

import {WorkspaceController, type Action, type Variant} from '@/lib/workspace';
import type {Job, PreviewResult, Context} from '@/lib/service';
import {createContext, useContext, useState, useEffect, useSyncExternalStore, useMemo} from 'react';

interface AppContextValue {
  job: Job | null;
  busy: boolean;
  error: string | null;
  restoring: boolean;
  tokenAvailable: boolean;
  setError: (error: string | null) => void;
  start: (file: File, context?: Context) => Promise<Job>;
  refresh: () => Promise<Job | null>;
  mutate: (action: Action, params: Record<string, unknown>) => Promise<Job>;
  erase: () => Promise<void>;
  preview: (variant: Variant) => Promise<PreviewResult>;
  page: (number: number, variant: Variant) => Promise<Blob>;
  download: (url: string) => Promise<Blob>;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({children}: {children: React.ReactNode}) {
  const controller = useState(() => new WorkspaceController())[0];
  const snapshot = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );

  useEffect(() => {
    controller.restore().catch(() => {});
    return () => controller.dispose();
  }, [controller]);

  useEffect(() => {
    const job = snapshot.job;
    if (!job || (job.status !== 'analyzing' && job.status !== 'rendering' && job.aiReview?.status !== 'running')) return;

    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;

    const poll = () => {
      timeoutId = setTimeout(async () => {
        if (cancelled) return;
        try {
          await controller.refresh();
        } catch {
          // ignore refresh errors
        }
        if (!cancelled) {
          const current = controller.getSnapshot();
          if (current.job?.status === 'analyzing' || current.job?.status === 'rendering' || current.job?.aiReview?.status === 'running') {
            poll();
          }
        }
      }, 1000);
    };

    poll();

    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [controller, snapshot.job]);

  const value = useMemo<AppContextValue>(
    () => ({
      job: snapshot.job,
      busy: snapshot.busy,
      error: snapshot.error,
      restoring: snapshot.restoring,
      tokenAvailable: snapshot.tokenAvailable,
      setError: controller.setError,
      start: controller.start,
      refresh: controller.refresh,
      mutate: controller.mutate,
      erase: controller.erase,
      preview: controller.preview,
      page: controller.page,
      download: controller.download,
    }),
    [snapshot, controller],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('AppContext가 제공되지 않았습니다. AppProvider 내부에서 useApp을 호출하세요.');
  }
  return context;
}

export type Preview = Awaited<ReturnType<WorkspaceController['preview']>>;
