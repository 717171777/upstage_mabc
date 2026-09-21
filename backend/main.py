from .llm_config import MODEL, configured
import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Scope, Receive, Send
from starlette.datastructures import UploadFile

from .hermes_extension import call, register_operations
from . import store, upstage
from .sharing_context import validate_context

# Use the StoreError from .store, not a local duplicate
StoreError = store.StoreError


# ---------------------------------------------------------------------------
# BodyLimitMiddleware  (pure ASGI, no framework dependency)
# ---------------------------------------------------------------------------
class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, jobs_bound: int = 10 * 1024 * 1024 + 64 * 1024,
                 other_bound: int = 2 * 1024 * 1024):
        self.app = app
        self.jobs_bound = jobs_bound
        self.other_bound = other_bound

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope["path"]

        if method not in ("POST", "PATCH"):
            await self.app(scope, receive, send)
            return

        # Exact path match for /jobs
        bound = self.jobs_bound if path == "/jobs" else self.other_bound

        # Check Content-Length header first
        content_length = 0
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    pass
                break

        if content_length > bound:
            await self._send_json_413(send)
            return

        # Collect body chunks, bounded
        chunks: list[bytes] = []
        total = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                if chunk:
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > bound:
                        await self._send_json_413(send)
                        return
                more_body = message.get("more_body", False)
            elif message["type"] == "http.disconnect":
                # Client disconnected during body collection - return immediately
                return
            else:
                break

        body_bytes = b"".join(chunks) if chunks else b""

        # Build a replay receive that first yields the cached body, then delegates
        original_receive = receive
        replay_done = False

        async def replay_receive() -> dict:
            nonlocal replay_done
            if not replay_done:
                replay_done = True
                return {
                    "type": "http.request",
                    "body": body_bytes,
                    "more_body": False,
                }
            return await original_receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _send_json_413(send: Send):
        body = json.dumps({"error": "요청 본문이 너무 큽니다.", "code": "BODY_TOO_LARGE"})
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body.encode("utf-8"),
        })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def auth(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or len(auth_header) <= 7:
        raise StoreError(401, "UNAUTHORIZED", "인증 헤더가 없거나 유효하지 않습니다.")
    token = auth_header[7:]
    if not token:
        raise StoreError(401, "UNAUTHORIZED", "토큰이 비어 있습니다.")
    return token


def _binary_to_response(data: dict) -> Response:
    b64 = data.get("binary")
    if not b64:
        raise StoreError(500, "INTERNAL", "이진 데이터가 없습니다.")
    media_type = data.get("mediaType", "application/octet-stream")
    filename = data.get("filename")
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise StoreError(500, "INTERNAL", "이진 데이터 디코딩에 실패했습니다.")
    headers = {
        "Content-Type": media_type,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if filename:
        safe_name = quote(filename, safe="")
        headers["Content-Disposition"] = f'attachment; filename*=UTF-8\'\'{safe_name}'
    return Response(content=raw, headers=headers, media_type=media_type)


async def invoke(request: Request, operation: str, jid: str, payload=None, **extras):
    token = auth(request)
    args = {
        "jobId": jid,
        "token": token,
        "payload": payload or {},
        **extras,
    }
    result = await asyncio.to_thread(call, operation, args)

    if isinstance(result, dict) and "binary" in result:
        return _binary_to_response(result)
    if isinstance(result, dict) and "error" in result:
        raise StoreError(400, result.get("code", "UNKNOWN"), result.get("message", "알 수 없는 오류"))
    return JSONResponse(content=result or {}, headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    register_operations()
    store.recover_jobs()
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


async def _cleanup_loop():
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(store.cleanup_expired)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(BodyLimitMiddleware)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
@app.exception_handler(StoreError)
async def store_error_handler(request: Request, exc: StoreError):
    return JSONResponse(
        content={"error": exc.message, "code": exc.code},
        status_code=exc.status,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        content={"error": "내부 서버 오류가 발생했습니다.", "code": "INTERNAL_ERROR"},
        status_code=500,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return JSONResponse(
        content={
            "ok": True,
            "aiConfigured": bool(os.environ.get("UPSTAGE_API_KEY", "").strip()) and configured(),
            "model": MODEL,
            "syntheticOnly": not upstage.user_documents_enabled(),
            "upstageRequired": upstage.upstage_required(),
            "platform": "Hermes",
        },
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/examples")
async def examples(format: str = None):
    if format not in ("docx", "pdf"):
        raise StoreError(422, "INVALID_FORMAT", "format은 docx 또는 pdf여야 합니다.")

    if format == "docx":
        fname = "docx-report_minutes-01.docx"
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        fname = "report_minutes-01.pdf"
        mime = "application/pdf"

    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "eval_v0" / "docs" / fname
    if not fixture_path.is_file():
        raise StoreError(404, "NOT_FOUND", f"파일 {fname}을 찾을 수 없습니다.")

    raw = fixture_path.read_bytes()
    safe_name = quote(f"synthetic-example.{format}", safe="")
    return Response(
        content=raw,
        media_type=mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8\'\'{safe_name}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/jobs")
async def create_job(request: Request):
    form = await request.form(max_files=1, max_fields=2)
    try:
        file = form.get("file")
        if not file:
            raise StoreError(422, "MISSING_FILE", "파일(file)이 필요합니다.")

        # Validate actual file instance using starlette.datastructures.UploadFile
        if not isinstance(file, UploadFile):
            raise StoreError(422, "INVALID_FILE", "파일 형식이 유효하지 않습니다.")

        ai_enabled_str = form.get("aiEnabled", "")
        if ai_enabled_str not in ("true", "false"):
            raise StoreError(422, "INVALID_AI_ENABLED", "aiEnabled는 'true' 또는 'false'여야 합니다.")

        raw_context = form.get('context', '{}')
        if not isinstance(raw_context, str):
            raise StoreError(422, 'invalid_context', '공유 상황은 JSON 문자열이어야 합니다.')
        try:
            initial_context = validate_context(json.loads(raw_context))
        except (json.JSONDecodeError, RecursionError):
            raise StoreError(422, 'invalid_context', '공유 상황의 JSON 형식이 올바르지 않습니다.')

        filename = file.filename or "upload"
        suffix = Path(filename).suffix.lower()
        if suffix not in (".pdf", ".docx"):
            raise StoreError(422, "INVALID_FORMAT", "지원하지 않는 파일 형식입니다. .pdf 또는 .docx만 허용됩니다.")

        # Read file in 64KiB chunks, max 10MiB
        chunks: list[bytes] = []
        total = 0
        max_bytes = 10 * 1024 * 1024
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise StoreError(413, "FILE_TOO_LARGE", "파일 크기가 10MiB 제한을 초과했습니다.")

        if total == 0:
            raise StoreError(422, "EMPTY_FILE", "파일이 비어 있습니다.")

        data_b64 = base64.b64encode(b"".join(chunks)).decode("ascii")
        fmt = suffix.lstrip(".")
        ai_enabled = ai_enabled_str == "true"

        result = await asyncio.to_thread(
            call, "create", {
                "data": data_b64,
                "filename": filename,
                "format": fmt,
                "aiEnabled": ai_enabled,
                "context": initial_context,
            }
        )

        if isinstance(result, dict) and "binary" in result:
            return _binary_to_response(result)
        if isinstance(result, dict) and "error" in result:
            raise StoreError(400, result.get("code", "CREATE_FAILED"), result.get("message", "생성 실패"))

        job = result.get("job") if isinstance(result, dict) else None
        token = result.get("token") if isinstance(result, dict) else None
        if not job or not token:
            raise StoreError(500, "CREATE_FAILED", "예상치 못한 생성 결과입니다.")

        return JSONResponse(
            content={"job": job, "token": token},
            status_code=201,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    finally:
        await form.close()


@app.get("/jobs/{jid}")
async def get_job(jid: str, request: Request):
    return await invoke(request, "get", jid)


@app.patch("/jobs/{jid}/plan")
async def plan_job(jid: str, request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise StoreError(422, "INVALID_JSON", "잘못된 JSON 본문입니다.")
    if not isinstance(payload, dict):
        raise StoreError(422, "INVALID_PAYLOAD", "페이로드는 JSON 객체여야 합니다.")
    return await invoke(request, "plan", jid, payload=payload)


@app.post("/jobs/{jid}/{action}")
async def job_action(jid: str, action: str, request: Request):
    allowed = {"manual", "resolve", "context", "render", "ack", "auto-export", "ai-review", "retry-analysis"}
    if action not in allowed:
        raise StoreError(404, "ACTION_NOT_FOUND", "알 수 없는 작업입니다.")
    try:
        payload = await request.json()
    except Exception:
        raise StoreError(422, "INVALID_JSON", "잘못된 JSON 본문입니다.")
    if not isinstance(payload, dict):
        raise StoreError(422, "INVALID_PAYLOAD", "페이로드는 JSON 객체여야 합니다.")
    return await invoke(request, action, jid, payload=payload)


@app.get("/jobs/{jid}/preview")
async def preview_job(jid: str, request: Request, variant: str = "original"):
    if variant not in ("original", "copy"):
        raise StoreError(422, "INVALID_VARIANT", "variant는 original 또는 copy여야 합니다.")
    return await invoke(request, "preview", jid, variant=variant)


@app.get("/jobs/{jid}/page/{page}")
async def page_job(jid: str, page: int, request: Request, variant: str = "original"):
    if variant not in ("original", "copy"):
        raise StoreError(422, "INVALID_VARIANT", "variant는 original 또는 copy여야 합니다.")
    return await invoke(request, "page", jid, page=page, variant=variant)


@app.get("/jobs/{jid}/download")
async def download_job(jid: str, request: Request, filename: str = None):
    return await invoke(request, "download", jid, filename=filename)


@app.delete("/jobs/{jid}")
async def delete_job(jid: str, request: Request):
    return await invoke(request, "delete", jid)
