"""Independent no-store and error redaction check, no network."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.http_policy import install

def test_unexpected_error_never_exposes_message_or_caches():
    app = FastAPI()
    install(app)
    @app.get('/fail')
    def fail():
        raise RuntimeError('private-document-sentinel')
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/fail')
        assert response.status_code == 500
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert 'private-document-sentinel' not in response.text
        assert response.json()['error'] == '서버 내부 오류가 발생했습니다.'
