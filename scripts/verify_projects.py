"""Live native-board, worker and failure/restart independence regression.

Creates TWO explicitly named local test repositories and native projects in the
factory-owned state. Uses real Hermes workers/Docker/native CLI (no doubles).
One project's deliberately failing verification must not poison the other.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forge.core import Forge,ForgeError

SPEC='''Write answer.txt containing exactly independent project\\n. Use the file tool, not shell file creation. No other files may change. Tests/checks run by the trusted controller. Simplifier may make no changes. Reviewer must read answer.txt and verify its exact contents, check Git HEAD and return explicit verdict. This is a deliberately tiny live infrastructure test, not an application feature.'''

def expect_failure(function, phrase):
    try:
        function()
    except ForgeError as error:
        if phrase not in str(error):
            raise AssertionError(f'Expected {phrase}, got {error}') from error
        return str(error)
    raise AssertionError('Expected bounded failure')

def main():
    forge=Forge()
    base=forge.forge_root/'probes/projects'
    base.mkdir(parents=True,exist_ok=True)
    spec=base/'answer-spec.md'
    spec.write_text(SPEC)
    registered={}
    for suffix in ('a','b'):
        slug='isolation-'+suffix
        repo=base/slug
        if slug not in forge._projects():
            repo.mkdir(exist_ok=True)
            subprocess.run(['git','init','-q',str(repo)],check=True)
            (repo/'README.md').write_text('Local live isolation test fixture. Not a production repository.\n')
            for args in (['add','README.md'],['-c','user.name=Forge Probe','-c','user.email=probe@localhost','commit','-qm','test: seed isolation fixture']):
                subprocess.run(['git','-C',str(repo),*args],check=True)
            verification=['python3','-c',"from pathlib import Path; assert Path('answer.txt').read_text() == 'independent project\\n'"]
            if suffix=='a':
                verification=['python3','-c','raise SystemExit(13)']
            forge.register(slug,repo,verify=verification)
        project=forge._project(slug)
        existing=project.get('tickets',{})
        if existing:
            tid=next(iter(existing))
        else:
            tid=forge.ticket(slug,'Live isolation '+suffix,spec_file=spec,allow_path=['answer.txt'])['id']
        registered[slug]={'ticket':tid,'repo':repo}
    a,b=registered['isolation-a']['ticket'],registered['isolation-b']['ticket']
    if not forge._project('isolation-a')['tickets'][a]['approved']:
        unapproved=expect_failure(lambda:forge.run('isolation-a',campaign='unapproved-check',ticket_ids=[a]),'not approved')
        forge.approve('isolation-a',a)
    else:
        unapproved='Previously verified; ticket already approved'
    forge.pause('isolation-a')
    paused=expect_failure(lambda:forge.run('isolation-a',campaign='pause-check',ticket_ids=[a]),'paused or failed')
    if not forge._project('isolation-b')['tickets'][b]['approved']:
        forge.approve('isolation-b',b)
    result=forge.run('isolation-b',campaign='independent-success',ticket_ids=[b],budget=900)
    assert result['tickets'][0]['status']=='review'
    failures=[]
    campaign_path=forge.forge_root/'campaigns/isolation-a/bounded-failure.json'
    previous=json.loads(campaign_path.read_text()) if campaign_path.exists() else {'tickets':{}}
    used=previous['tickets'].get(a,{}).get('attempts',{}).get('builder',0)
    for attempt in range(used,2):
        forge.resume('isolation-a')
        failures.append(expect_failure(lambda:forge.run('isolation-a',campaign='bounded-failure',ticket_ids=[a],budget=600),'exited 13'))
        forge=Forge()  # Fresh controller must preserve consumed attempts and deadline.
    saved=json.loads(campaign_path.read_text())['tickets'][a]
    assert saved['attempts']['builder']==2 and saved['status']=='failed' and 'exited 13' in saved['error']
    forge.resume('isolation-a')
    try:
        forge.run('isolation-a',campaign='bounded-failure',ticket_ids=[a])
    except ForgeError as error:
        exhausted=str(error)
        # Native Kanban may move an exhausted card into triage before Forge's
        # own stage cap is reached; neither may grant a third worker attempt.
        assert 'attempt limit' in exhausted or 'native card did not enter running state' in exhausted
    else:
        raise AssertionError('Exhausted campaign launched work')
    assert json.loads(campaign_path.read_text())['tickets'][a]['attempts']['builder']==2
    forge.pause('isolation-a')  # Leave the deliberately failing fixture disabled.
    status_a,status_b=forge.status('isolation-a'),forge.status('isolation-b')
    assert status_a['paused'] and not status_b['failed'] and not status_b['paused']
    for slug,record in registered.items():
        assert not (record['repo']/'answer.txt').exists(), 'Source repository was integrated without approval'
        cards=forge.status(slug)['native_cards']
        assert cards is not None
    output={'native_projects':2,'independent_pause':True,'independent_failure':True,'source_repositories_unchanged':True,'restart_attempt_exhaustion':True,'unapproved_gate':unapproved,'paused_gate':paused,'passing_project':result,'failure_attempts':saved['attempts']['builder'],'exhausted':exhausted}
    evidence=base/'verification.json'
    evidence.write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(output))

if __name__=='__main__':
    main()
