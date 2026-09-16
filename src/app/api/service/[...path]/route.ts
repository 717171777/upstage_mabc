import type { NextRequest } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const BACKEND_URL = process.env.GARIMI_BACKEND_URL || 'http://127.0.0.1:8000';
if (!/^https?:\/\//.test(BACKEND_URL)) {
  throw new Error('GARIMI_BACKEND_URL must start with http:// or https://');
}

const HEX_ID = /^[0-9a-f]{32}$/;
const POS_INT = /^[1-9][0-9]*$/;
const CREATE_LIMIT = 10 * 1024 * 1024 + 64 * 1024;
const OTHER_LIMIT = 2 * 1024 * 1024;

function isPathSafe(s: string): boolean {
  return s.length > 0 && !s.includes('/') && !s.includes('.');
}

interface RouteConfig {
  backendPath: string;
  method: string;
  bodyLimit: number;
  allowedQueries: ReadonlySet<string>;
  queryValidators: Readonly<Record<string, (v: string) => boolean>>;
}

function resolveRoute(parts: string[], method: string): RouteConfig | null {
  if (!parts.every(isPathSafe) || parts.length === 0) return null;
  const [first, ...rest] = parts;

  if (first === 'health') {
    if (rest.length !== 0 || method !== 'GET') return null;
    return { backendPath: '/health', method: 'GET', bodyLimit: 0, allowedQueries: new Set(), queryValidators: {} };
  }

  if (first === 'examples') {
    if (rest.length !== 0 || method !== 'GET') return null;
    return {
      backendPath: '/examples', method: 'GET', bodyLimit: 0,
      allowedQueries: new Set(['format']),
      queryValidators: { format: (v: string) => v === 'pdf' || v === 'docx' }
    };
  }

  if (first !== 'jobs') return null;
  if (rest.length === 0) {
    if (method !== 'POST') return null;
    return { backendPath: '/jobs', method: 'POST', bodyLimit: CREATE_LIMIT, allowedQueries: new Set(), queryValidators: {} };
  }
  if (!HEX_ID.test(rest[0])) return null;
  const id = rest[0];

  if (rest.length === 1) {
    if (method === 'GET' || method === 'DELETE') {
      return { backendPath: `/jobs/${id}`, method, bodyLimit: 0, allowedQueries: new Set(), queryValidators: {} };
    }
    return null;
  }

  if (rest.length === 2) {
    const action = rest[1];
    if (action === 'plan' && method === 'PATCH') {
      return { backendPath: `/jobs/${id}/plan`, method: 'PATCH', bodyLimit: OTHER_LIMIT, allowedQueries: new Set(), queryValidators: {} };
    }
    if (['manual', 'resolve', 'context', 'render', 'ack', 'auto-export', 'ai-review', 'retry-analysis'].includes(action) && method === 'POST') {
      return { backendPath: `/jobs/${id}/${action}`, method: 'POST', bodyLimit: OTHER_LIMIT, allowedQueries: new Set(), queryValidators: {} };
    }
    if (action === 'preview' && method === 'GET') {
      return {
        backendPath: `/jobs/${id}/preview`, method: 'GET', bodyLimit: 0,
        allowedQueries: new Set(['variant']),
        queryValidators: { variant: (v: string) => v === 'original' || v === 'copy' }
      };
    }
    if (action === 'download' && method === 'GET') {
      return {
        backendPath: `/jobs/${id}/download`, method: 'GET', bodyLimit: 0,
        allowedQueries: new Set(['filename']),
        queryValidators: {}
      };
    }
    return null;
  }

  if (rest.length === 3 && rest[1] === 'page' && POS_INT.test(rest[2]) && method === 'GET') {
    return {
      backendPath: `/jobs/${id}/page/${rest[2]}`, method: 'GET', bodyLimit: 0,
      allowedQueries: new Set(['variant']),
      queryValidators: { variant: (v: string) => v === 'original' || v === 'copy' }
    };
  }

  return null;
}

function validateQuery(config: RouteConfig, request: Request): boolean {
  const url = new URL(request.url);
  for (const [key, val] of url.searchParams) {
    if (!config.allowedQueries.has(key)) return false;
    const validator = config.queryValidators[key];
    if (validator && !validator(val)) return false;
  }
  return true;
}

function readBody(request: Request, limit: number, signal: AbortSignal): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    if (limit === 0) return resolve(new Uint8Array());
    if (signal.aborted) {
      reject(new Error('ABORTED'));
      return;
    }
    const chunks: Uint8Array[] = [];
    let total = 0;
    if (request.body === null) return resolve(new Uint8Array());
    const reader = request.body.getReader();

    const abortHandler = () => {
      reader.cancel().catch(() => {});
      reject(new Error('ABORTED'));
    };
    signal.addEventListener('abort', abortHandler, { once: true });

    const process = async () => {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (total + value.byteLength > limit) {
            reader.cancel().catch(() => {});
            signal.removeEventListener('abort', abortHandler);
            reject(new Error('BODY_OVERLIMIT'));
            return;
          }
          total += value.byteLength;
          chunks.push(value);
        }
        const buf = new Uint8Array(total);
        let offset = 0;
        for (const c of chunks) {
          buf.set(c, offset);
          offset += c.byteLength;
        }
        resolve(buf);
      } catch (err) {
        reject(err);
      } finally {
        signal.removeEventListener('abort', abortHandler);
      }
    };
    process();
  });
}

function forwardHeaders(request: Request): Record<string, string> {
  const headers: Record<string, string> = {};
  const contentType = request.headers.get('Content-Type');
  if (contentType) headers['Content-Type'] = contentType;
  const auth = request.headers.get('Authorization');
  if (auth && auth.startsWith('Bearer ')) headers['Authorization'] = auth;
  return headers;
}

function koreanMessage(code: number): string {
  if (code === 404) return '요청한 경로를 찾을 수 없습니다.';
  if (code === 405) return '허용되지 않은 메서드입니다.';
  if (code === 413) return '요청 본문이 너무 큽니다.';
  if (code === 502) return '백엔드 서버 연결 오류입니다.';
  if (code === 503) return '요청 시간이 초과되었습니다.';
  if (code === 504) return '백엔드 서버 응답 시간이 초과되었습니다.';
  return '오류가 발생했습니다.';
}

function errorResponse(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff'
    }
  });
}

async function proxy(request: NextRequest, params: Promise<{ path: string[] }>): Promise<Response> {
  const { path } = await params;
  const method = request.method;
  const config = resolveRoute(path, method);

  if (!config) {
    return errorResponse(404, koreanMessage(404));
  }

  if (!validateQuery(config, request)) {
    return errorResponse(404, koreanMessage(404));
  }

  const origin = request.headers.get('Origin');
  const requestOrigin = (() => {
    const envOrigin = process.env.GARIMI_PUBLIC_ORIGIN;
    if (envOrigin) {
      try {
        return new URL(envOrigin).origin;
      } catch {
        throw new Error("Invalid GARIMI_PUBLIC_ORIGIN");
      }
    }
    return new URL(request.url).origin;
  })();
  if (origin && origin !== requestOrigin && ['POST', 'PATCH', 'DELETE'].includes(method)) {
    return errorResponse(404, koreanMessage(404));
  }

  const combined = AbortSignal.any([request.signal, AbortSignal.timeout(180000)]);

  let body: Uint8Array | null = null;
  if (config.bodyLimit > 0) {
    if (request.body === null) {
      body = new Uint8Array();
    } else {
      const cl = request.headers.get('Content-Length');
      if (cl && Number(cl) > config.bodyLimit) {
        return errorResponse(413, koreanMessage(413));
      }
      if (combined.aborted) {
        return errorResponse(504, koreanMessage(504));
      }
      try {
        body = await readBody(request, config.bodyLimit, combined);
      } catch (err: unknown) {
        if (err instanceof Error && err.message === 'BODY_OVERLIMIT') {
          return errorResponse(413, koreanMessage(413));
        }
        if (combined.aborted) {
          return errorResponse(504, koreanMessage(504));
        }
        return errorResponse(502, koreanMessage(502));
      }
    }
  }

  const headers = forwardHeaders(request);
  const target = new URL(config.backendPath, BACKEND_URL);
  target.search = new URL(request.url).search;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: config.method,
      headers,
      body: body ? (body.buffer as ArrayBuffer) : undefined,
      signal: combined,
      cache: 'no-store',
      redirect: 'manual'
    });
  } catch (err: unknown) {
    if (combined.aborted) {
      return errorResponse(504, koreanMessage(504));
    }
    return errorResponse(502, koreanMessage(502));
  }

  if (upstream.status >= 300 && upstream.status < 400) {
    return errorResponse(502, koreanMessage(502));
  }

  const responseHeaders: Record<string, string> = {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff'
  };

  const ct = upstream.headers.get('Content-Type');
  if (ct) responseHeaders['Content-Type'] = ct;

  const cd = upstream.headers.get('Content-Disposition');
  if (cd) responseHeaders['Content-Disposition'] = cd;

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders
  });
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  return proxy(request, context.params);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  return proxy(request, context.params);
}

export async function PATCH(request: NextRequest, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  return proxy(request, context.params);
}

export async function DELETE(request: NextRequest, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  return proxy(request, context.params);
}
