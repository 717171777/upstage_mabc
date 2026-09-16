"""Verify real shutdown behavior with a stubborn descendant in its own test group."""
import importlib.util,subprocess,sys,os,signal,time,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('supervisor',Path(__file__).resolve().parents[1]/'deploy/start.py')
supervisor=importlib.util.module_from_spec(spec);spec.loader.exec_module(supervisor)

def test_cleanup_removes_term_ignoring_descendant():
 child='import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("READY",flush=True);time.sleep(60)'
 parent='import subprocess,sys,time;p=subprocess.Popen([sys.executable,"-c",'+repr(child)+'],stdout=subprocess.PIPE,text=True);p.stdout.readline();print(p.pid,flush=True);time.sleep(60)'
 proc=subprocess.Popen([sys.executable,'-c',parent],stdout=subprocess.PIPE,text=True,start_new_session=True)
 descendant=int(proc.stdout.readline());started=time.monotonic()
 try:
  supervisor._cleanup_process(proc,'test',proc.pid)
  assert proc.poll() is not None
  assert time.monotonic()-started<12
  status=subprocess.run(['ps','-p',str(descendant),'-o','stat='],capture_output=True,text=True).stdout.strip()
  assert not status or status.startswith('Z'),f'descendant still running: {status}'
 finally:
  try:os.killpg(proc.pid,signal.SIGKILL)
  except ProcessLookupError:pass
  proc.wait(timeout=2)

def test_cancelled_health_wait_does_not_connect():
 started=time.monotonic()
 assert supervisor._wait_health('http://127.0.0.1:1',timeout=30,should_stop=lambda:True) is False
 assert time.monotonic()-started<1
