"""Split local preview deployment; defaults to replacing the frontend only.
Keys are inherited by the backend process only. Never print inspect/env output.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
D = ['/usr/local/bin/docker', '--config', str(ROOT/'verification/docker-public-config'),
     '--host', 'unix:///Users/a1717771/.docker/run/docker.sock']
FRONT = 'garimi-preview'
BACK = 'garimi-analysis'
NETWORK = 'garimi-services'
VOLUME = 'garimi-preview-data'
IMAGE = 'garimi:local'


def run(*args, check=True):
    return subprocess.run(D + list(args), check=check, capture_output=True, text=True)


def exists(name):
    return run('inspect', name, check=False).returncode == 0


def healthy(url, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('서비스 연결 확인 실패')


def active_count(container):
    # Print counts only, never document names, context, candidates, or credentials.
    script = '''import pathlib,json,time
n=sum(1 for p in pathlib.Path('/data').glob('.admission-*'))
for p in pathlib.Path('/data/jobs').glob('*/job.json'):
 j=json.loads(p.read_text())
 if j.get('_expiresEpoch',0)>time.time() and (j.get('status') in ('analyzing','rendering') or (j.get('aiReview') or {}).get('status')=='running'):n+=1
print(n)'''
    return int(run('exec', container, 'python3', '-c', script).stdout.strip())


def set_drain(container, enabled):
    expression = "p.touch(mode=0o600)" if enabled else "p.unlink(missing_ok=True)"
    run('exec', container, 'python3', '-c', "from pathlib import Path;p=Path('/data/.draining');" + expression)


def start_backend():
    from dotenv import dotenv_values
    values = dotenv_values(Path.home()/'.hermes/.env')
    key = os.environ.get('UPSTAGE_API_KEY') or values.get('UPSTAGE_API_KEY')
    claude_key = os.environ.get('ANTHROPIC_API_KEY') or values.get('ANTHROPIC_API_KEY')
    if not claude_key:
        raise RuntimeError('Claude 연결 키가 없습니다')
    if not key:
        raise RuntimeError('Upstage 연결 키가 없습니다')
    os.environ['UPSTAGE_API_KEY'] = key
    os.environ['ANTHROPIC_API_KEY'] = claude_key
    run('run', '-d', '--name', BACK, '--restart', 'unless-stopped', '--network', NETWORK,
        '--mount', f'type=volume,source={VOLUME},target=/data',
        '--env', 'UPSTAGE_API_KEY', '--env', 'ANTHROPIC_API_KEY',
        '--env', 'GARIMI_CLAUDE_MODEL=claude-sonnet-5', '--env', 'GARIMI_PUBLIC_ORIGIN=http://localhost:3002',
        '--env', 'GARIMI_REQUIRE_UPSTAGE=1', '--env', 'GARIMI_ALLOW_USER_DOCUMENTS=1',
        '--security-opt', 'no-new-privileges:true', '--stop-timeout', '60',
        '--health-cmd', "python3 -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)\"",
        IMAGE, 'python3', '-m', 'uvicorn', 'backend.http_policy:app', '--host', '0.0.0.0', '--port', '8000',
        '--workers', '1', '--no-access-log')
    os.environ.pop('UPSTAGE_API_KEY', None)
    os.environ.pop('ANTHROPIC_API_KEY', None)


def start_frontend():
    run('run', '-d', '--name', FRONT, '--restart', 'unless-stopped', '--network', NETWORK,
        '--publish', '127.0.0.1:3002:3000', '--env', f'GARIMI_BACKEND_URL=http://{BACK}:8000',
        '--env', 'GARIMI_PUBLIC_ORIGIN=http://localhost:3002',
        '--security-opt', 'no-new-privileges:true', IMAGE,
        'node', 'node_modules/next/dist/bin/next', 'start', '--hostname', '0.0.0.0', '--port', '3000')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', action='store_true', help='분석 서버도 안전하게 교체')
    args = parser.parse_args()
    migration = not exists(BACK)
    replace_backend = migration or args.backend
    current_backend = FRONT if migration else BACK
    # The legacy server lacks admission control: refuse migration while any job runs.
    if migration and active_count(current_backend):
        raise RuntimeError('실행 중인 작업이 있어 분리를 보류했습니다. 작업 완료 후 다시 실행하세요.')
    if replace_backend and not migration:
        set_drain(BACK, True)
        try:
            deadline = time.monotonic() + 900
            while active_count(BACK):
                if time.monotonic() > deadline:
                    raise RuntimeError('분석이 진행 중이므로 업데이트를 보류했습니다.')
                time.sleep(2)
        except BaseException:
            set_drain(BACK, False)
            raise
    run('network', 'create', NETWORK, check=False)
    run('volume', 'create', VOLUME)
    front_saved = back_saved = False
    try:
        if exists(FRONT):
            run('stop', '--time', '60', FRONT)
            run('rename', FRONT, FRONT+'-rollback')
            front_saved = True
        if replace_backend:
            if not migration:
                run('stop', '--time', '60', BACK)
                run('rename', BACK, BACK+'-rollback')
                back_saved = True
            start_backend()
        start_frontend()
        healthy('http://localhost:3002/api/service/health')
        if replace_backend:
            set_drain(BACK, False)
    except BaseException:
        if exists(FRONT): run('rm', '-f', FRONT)
        if replace_backend and exists(BACK): run('rm', '-f', BACK)
        if back_saved:
            run('rename', BACK+'-rollback', BACK)
            run('start', BACK)
            set_drain(BACK, False)
        if front_saved:
            run('rename', FRONT+'-rollback', FRONT)
            run('start', FRONT)
        raise RuntimeError('새 서비스 연결에 실패해 이전 서비스를 복구했습니다.') from None
    if front_saved: run('rm', FRONT+'-rollback')
    if back_saved: run('rm', BACK+'-rollback')
    print('localhost:3002 업데이트 완료. ' + ('분석 서버를 분리·업데이트했습니다.' if replace_backend else '분석 서버를 유지하고 화면만 업데이트했습니다.'))


if __name__ == '__main__':
    main()
