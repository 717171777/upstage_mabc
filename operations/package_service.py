"""Build a source-only service bundle from an explicit allowlist."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SINGLES=('package.json','package-lock.json','next.config.ts','tsconfig.json','next-env.d.ts',
 'postcss.config.mjs','eslint.config.mjs','Dockerfile','.dockerignore','.gitignore','AGENTS.md',
 'Dockerfile.backend','Dockerfile.backend.dockerignore','CLAUDE-DEPLOYMENT.md',
 'VERIFICATION-2026-09-21.md',
 'START-HERE.md','IMPLEMENTATION-STATUS.md','SERVICE-RUNTIME-POLICY.md',
 'PRD-IMPLEMENTATION-ANCHOR.md','REVIEW-AND-AUTO-COPY-2026-09-16.md','RESUMABLE-ANALYSIS-2026-09-16.md')
TREES=('src','public','backend','deploy','reference/schemas','fixtures/eval_v0')
TESTS=('verification/browser_auto_copy.cjs','verification/browser_decision_keyboard.cjs',
 'verification/browser_full_redaction.cjs','verification/audit_local_detection.py',
 'verification/test_repeat_decisions.cjs','verification/test_masking.cjs',
 'verification/email_preset_cases.json','verification/semantic_preset_cases.json')

def files():
 paths={ROOT/p for p in (*SINGLES,*TESTS,'operations/package_service.py')}
 paths.update((ROOT/'verification').glob('test_*.py'))
 for directory in TREES:
  paths.update((ROOT/directory).rglob('*'))
 for p in sorted(paths):
  if not p.is_file():continue
  rel=p.relative_to(ROOT)
  if p.is_symlink() or any(part.startswith('.') and part not in ('.gitignore','.dockerignore') for part in rel.parts):continue
  if '__pycache__' in rel.parts or p.suffix in ('.pyc','.log'):continue
  if p.name.endswith('.private.json') or p.name.startswith('.env'):continue
  yield p,rel.as_posix()

def main():
 target=ROOT/'releases'/'garimi-service-claude-sonnet-2026-09-20.zip';target.parent.mkdir(exist_ok=True)
 records=[]
 with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
  for p,name in files():
   data=p.read_bytes();z.writestr('garimi/'+name,data)
   records.append({'path':name,'sha256':hashlib.sha256(data).hexdigest()})
  z.writestr('garimi/SOURCE-MANIFEST.json',json.dumps({'files':records},ensure_ascii=False,indent=2))
 print(json.dumps({'package':str(target),'files':len(records),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()},ensure_ascii=False))

if __name__=='__main__':main()
