from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError

_KOREAN = {
    400: "잘못된 요청입니다.",
    401: "인증이 필요합니다.",
    403: "접근이 거부되었습니다.",
    404: "요청한 리소스를 찾을 수 없습니다.",
    405: "허용되지 않은 메서드입니다.",
    413: "요청 크기가 너무 큽니다.",
    415: "지원하지 않는 미디어 타입입니다.",
    422: "요청 검증에 실패했습니다.",
}
_500_MSG = "서버 내부 오류가 발생했습니다."


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _headers(request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse(
                status_code=500,
                content={"error": _500_MSG, "code": 500},
            )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(HTTPException)
    async def _http_exc(request, exc):
        code = exc.status_code
        msg = _KOREAN.get(code, _500_MSG)
        return JSONResponse(status_code=code, content={"error": msg, "code": code})

    @app.exception_handler(RequestValidationError)
    async def _val_exc(request, exc):
        return JSONResponse(
            status_code=422,
            content={"error": "요청 검증에 실패했습니다.", "code": 422},
        )

    @app.exception_handler(Exception)
    async def _generic_exc(request, exc):
        response = JSONResponse(
            status_code=500,
            content={"error": _500_MSG, "code": 500},
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


from .main import app

install(app)
