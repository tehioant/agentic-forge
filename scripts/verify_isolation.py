"""Live whole-agent confinement probe; uses actual Docker, not mocks."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forge.runtime import DockerRuntime

PROBE = r'''
import json,os,pathlib
w=pathlib.Path('/workspace')
results={'nonroot':os.getuid()!=0,'docker_socket_absent':not pathlib.Path('/var/run/docker.sock').exists(),'personal_home_absent':not pathlib.Path('/home/ops/.hermes/auth.json').exists(),'other_project_absent':not pathlib.Path('/sibling').exists()}
try:
 (w/'.git/forbidden-write').write_text('probe')
 results['git_readonly']=False
except PermissionError: results['git_readonly']=True
except OSError as e: results['git_readonly']=e.errno==30
try:
 pathlib.Path('/etc/forge-forbidden-write').write_text('probe')
 results['root_readonly']=False
except PermissionError: results['root_readonly']=True
except OSError as e: results['root_readonly']=e.errno==30
print(json.dumps(results))
assert all(results.values()),results
'''

def main():
    home=Path(os.environ.get('FORGE_HOME',str(Path.home()/'.local/share/agentic-forge')))
    runtime=DockerRuntime(home)
    scratch=home/'probes'
    scratch.mkdir(exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(dir=scratch) as d:
        p=Path(d)
        repos=[]
        for n in ('project-a','project-b'):
            repo=p/n
            (repo/'.git').mkdir(parents=True)
            repos.append(repo)
        def check(repo):
            name='forge-probe-'+uuid.uuid4().hex[:12]
            args=runtime.command(repo,name=name)
            args += ['--entrypoint','python3',runtime.image,'-c',PROBE]
            output=runtime.execute(args,30,scratch/(repo.name+'.log'),name=name)
            return {repo.name:json.loads(output)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(check,repos))
        print(json.dumps({'live_confinement':results,'parallel_projects':2}))
        name='forge-probe-readonly-'+uuid.uuid4().hex[:8]
        args=runtime.command(repos[0],name=name,read_only=True)
        readonly="import pathlib; p=pathlib.Path('/workspace/no-write');\ntry:\n p.write_text('x')\n raise AssertionError('review workspace writable')\nexcept OSError as e:\n assert e.errno in (13,30); print('review workspace read-only verified')"
        args+=['--entrypoint','python3',runtime.image,'-c',readonly]
        print(runtime.execute(args,30,scratch/'review-readonly.log',name=name).strip())
    if '--inference' in sys.argv:
        result=runtime.run('reviewer',ROOT,'Read /workspace/README.md using the file tool and get exact git HEAD via terminal. Verify /home/ops/.hermes/auth.json and /var/run/docker.sock are absent using a harmless read-only terminal command. Do not change any files or read /opt/data/auth.json. This is a runtime access test, not a code review. Return the requested JSON only, passed=true only if actual tools confirm the reads and absence checks.',timeout=180,log=scratch/'inference-smoke.log',read_only=True)
        print(json.dumps({'live_inference':result}))

if __name__=='__main__':
    main()
