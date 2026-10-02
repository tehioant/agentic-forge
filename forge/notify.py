"""Metadata-only best-effort change notifications, with no LLM or worker launch."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from .core import _atomic_json, _json_load


def collect(home: Path) -> tuple[list[str], dict]:
    registry=_json_load(home/'projects.json',{})
    previous=_json_load(home/'notification-state.json',{})
    current={}
    messages=[]
    for slug, project in sorted(registry.items()):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',slug):
            continue
        local=_json_load(home/'projects'/slug/'state.json',{})
        current[slug]={'failed':bool(local.get('failed')),'paused':bool(local.get('paused')),
                       'review':[tid for tid,t in sorted(project.get('tickets',{}).items()) if t.get('status')=='review']}
        before=previous.get(slug,{})
        if current[slug]['failed'] and not before.get('failed'):
            messages.append(f'Agentic Forge — **{slug} blocked**. Inspect its status/evidence before resuming. No merge or deployment performed.')
        if current[slug]['paused'] and not before.get('paused'):
            messages.append(f'Agentic Forge — **{slug} paused**. Other projects are unchanged.')
        for tid in current[slug]['review']:
            if tid not in before.get('review',[]) and re.fullmatch(r'[A-Za-z0-9._-]{1,100}',tid):
                messages.append(f'Agentic Forge — **{slug} / {tid} is review-ready** after the local pipeline. Publication/CI and human merge remain separate gates.')
    return messages,current


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--home',type=Path,default=Path(os.environ.get('FORGE_HOME',str(Path.home()/'.local/share/agentic-forge'))))
    args=p.parse_args(argv)
    messages,state=collect(args.home)
    if messages:
        print('\n\n'.join(messages),flush=True)
    _atomic_json(args.home/'notification-state.json',state)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
