"""Fallback diagnostic runner when pytest is unavailable; not a pytest substitute.

Runs ordinary test_ functions and tmp_path-only functions, reports all skips.
Each test file executes in its own process to isolate imports and global state.
"""
import concurrent.futures
import inspect
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import traceback

ROOT=Path(__file__).resolve().parents[1]

def run_one(file):
    sys.path.insert(0, str(Path(file).resolve().parent))
    try:
        namespace=runpy.run_path(file)
    except ModuleNotFoundError as exc:
        if exc.name=='pytest':
            print('SKIP_FILE pytest missing',file);return 0
        raise
    failures=0
    for name,func in namespace.items():
        if not name.startswith('test_') or not callable(func):continue
        params=list(inspect.signature(func).parameters)
        if params not in ([],['tmp_path']):
            print('SKIP_FIXTURE',name,params);continue
        try:
            with tempfile.TemporaryDirectory(prefix='direct_regression_') as temp:
                func(Path(temp)) if params else func()
            print('PASS',name)
        except ModuleNotFoundError as exc:
            if exc.name=='pytest': print('SKIP_DEPENDENCY',name,'pytest')
            else: failures+=1;print('FAIL',name);traceback.print_exc()
        except Exception:
            failures+=1; print('FAIL',name);traceback.print_exc()
    return bool(failures)

def main():
    if len(sys.argv)>1:return run_one(sys.argv[1])
    paths=sorted((ROOT/'tests').glob('test_*.py'))
    env={**os.environ,'PYTHONPATH':str(ROOT)}
    def run_file(path):
        r=subprocess.run([sys.executable,__file__,str(path)],cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        return path,r
    counts={'PASS':0,'FAIL':0,'SKIP_FILE':0,'SKIP_FIXTURE':0,'SKIP_DEPENDENCY':0}
    failure=False
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for path,r in pool.map(run_file,paths):
            print('FILE',path.name, 'exit',r.returncode,flush=True)
            print(r.stdout,flush=True)
            failure |= bool(r.returncode)
            for line in r.stdout.splitlines():
                key=line.split(' ',1)[0]
                if key in counts:counts[key]+=1
    print('DIRECT_REGRESSION_SUMMARY',counts,flush=True)
    return int(failure)

if __name__=='__main__':sys.exit(main())
