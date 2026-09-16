import os
import sys
import signal
import subprocess
import time
import urllib.request


def _log(msg: str) -> None:
    print(msg, flush=True)


def _validate_port(port: int) -> bool:
    return 1 <= port <= 65535


def _wait_health(url: str, timeout: float = 30.0, interval: float = 0.25, should_stop=lambda: False) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if should_stop():
            return False
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _kill_tree(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _cleanup_process(proc: subprocess.Popen, name: str, pgid: int) -> None:
    if pgid > 0:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        proc.poll()
        if pgid > 0:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                return
        time.sleep(0.05)
    if pgid > 0:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.wait(timeout=1)
    except Exception:
        pass


def main() -> None:
    port_str = os.environ.get("PORT", "3000")
    try:
        port = int(port_str)
    except ValueError:
        _log(f"invalid PORT: {port_str}")
        sys.exit(2)
    if not _validate_port(port):
        _log(f"PORT out of range: {port}")
        sys.exit(2)

    backend_url = "http://127.0.0.1:8000"
    backend_health_url = f"{backend_url}/health"
    frontend_health_url = f"http://127.0.0.1:{port}/api/service/health"

    backend_proc = None
    frontend_proc = None
    backend_pgid = 0
    frontend_pgid = 0
    shutdown_requested = False

    def _handle_signal(sig: signal.Signals, frame) -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True
        _log("received signal, shutting down")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        backend_env = os.environ.copy()
        backend_env["PYTHONUNBUFFERED"] = "1"
        backend_cmd = [
            sys.executable, "-m", "uvicorn",
            "backend.http_policy:app",
            "--host", "127.0.0.1",
            "--port", "8000",
            "--workers", "1",
            "--no-access-log",
        ]
        backend_proc = subprocess.Popen(
            backend_cmd,
            env=backend_env,
            start_new_session=True,
        )
        backend_pgid = backend_proc.pid
        _log("backend starting")

        if not _wait_health(backend_health_url, timeout=30.0, should_stop=lambda: shutdown_requested):
            _log("backend health check failed")
            if shutdown_requested:
                sys.exit(0)
            if backend_proc.poll() is None:
                _cleanup_process(backend_proc, "backend", backend_pgid)
            sys.exit(1)
        _log("backend healthy")
        if shutdown_requested:
            sys.exit(0)

        frontend_env = os.environ.copy()
        frontend_env["PORT"] = str(port)
        frontend_cmd = [
            "node",
            "node_modules/next/dist/bin/next",
            "start",
            "--hostname", "0.0.0.0",
            "--port", str(port),
        ]
        frontend_proc = subprocess.Popen(
            frontend_cmd,
            cwd="/app",
            env=frontend_env,
            start_new_session=True,
        )
        frontend_pgid = frontend_proc.pid
        _log("frontend starting")

        if not _wait_health(frontend_health_url, timeout=30.0, should_stop=lambda: shutdown_requested):
            _log("frontend health check failed")
            if shutdown_requested:
                sys.exit(0)
            if frontend_proc.poll() is None:
                _cleanup_process(frontend_proc, "frontend", frontend_pgid)
            if backend_proc.poll() is None:
                _cleanup_process(backend_proc, "backend", backend_pgid)
            sys.exit(1)
        _log("frontend healthy")

        while True:
            if shutdown_requested:
                sys.exit(0)
            backend_rc = backend_proc.poll()
            frontend_rc = frontend_proc.poll()
            if backend_rc is not None:
                _log(f"backend exited with {backend_rc}")
                if frontend_proc.poll() is None:
                    _cleanup_process(frontend_proc, "frontend", frontend_pgid)
                sys.exit(1)
            if frontend_rc is not None:
                _log(f"frontend exited with {frontend_rc}")
                if backend_proc.poll() is None:
                    _cleanup_process(backend_proc, "backend", backend_pgid)
                sys.exit(1)
            time.sleep(0.25)

    finally:
        if backend_proc:
            _cleanup_process(backend_proc, "backend", backend_pgid)
        if frontend_proc:
            _cleanup_process(frontend_proc, "frontend", frontend_pgid)


if __name__ == "__main__":
    main()
