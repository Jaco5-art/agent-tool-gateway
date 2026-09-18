"""Local manual review demo. No model calls; all refunds are simulated."""
import argparse
import json
import os
import time
from pathlib import Path
from .identity import IdentityRegistry, local_session
from .backend import Backend
from .service import Service
from .policy.engine import PolicyBlocked


def save_secret(path,data):
    # Restrictive POSIX permissions; Windows access inherits the user's directory ACL.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(data,f)

def run(command,root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    requester_file=root/'requester.secret.json'
    reviewer_file=root/'reviewer.secret.json'
    if command=='prepare':
        if requester_file.exists() or reviewer_file.exists(): raise ValueError('Demo already exists. Use a new --workdir; do not reset an existing approval.')
        registry,token=local_session(root/'identity.sqlite','manual-demo')
        service=Service(Backend(root/'business.sqlite'),root/'audit.jsonl','manual-demo',registry=registry,token=token)
        reviewer=service.approvals.register_reviewer('manual-reviewer','demo-tenant',['refund.issue'],['order:order_438'],time.time()+3600)
        save_secret(requester_file,{'session_token':token})
        save_secret(reviewer_file,{'reviewer_token':reviewer})
        try: service.call('refund.issue',{'order_id':'order_438','amount_minor':1200,'currency':'GBP','reason':'Demo'})
        except PolicyBlocked as exc:
            aid=exc.decision.approval_id
            if not aid: raise
            (root/'request.json').write_text(json.dumps({'approval_id':aid}),encoding='utf-8')
            return service.approvals.get(aid)
    registry=IdentityRegistry(root/'identity.sqlite')
    requester=json.loads(requester_file.read_text(encoding='utf-8'))
    service=Service(Backend(root/'business.sqlite'),root/'audit.jsonl','manual-demo',registry=registry,token=requester['session_token'])
    aid=json.loads((root/'request.json').read_text(encoding='utf-8'))['approval_id']
    if command=='show': return service.approvals.get(aid)
    if command in {'approve','reject'}:
        reviewer=json.loads(reviewer_file.read_text(encoding='utf-8'))
        return service.approvals.decide(aid,reviewer['reviewer_token'],command)
    token_file=root/'execution.secret.json'
    if command=='issue':
        if token_file.exists(): raise ValueError('JIT credential already exists; use inspect-token.')
        token,metadata=service.issue_jit(aid)
        save_secret(token_file,{'jit_token':token})
        return metadata
    if command in {'inspect-token','revoke-token'}:
        token=json.loads(token_file.read_text(encoding='utf-8'))['jit_token']
        metadata=service.jit.inspect(token)
        if command=='revoke-token':
            service.jit.revoke(metadata['token_id'])
            metadata=service.jit.inspect(token)
        return metadata
    if command=='resume':
        token=json.loads(token_file.read_text(encoding='utf-8'))['jit_token'] if token_file.exists() else None
        return service.resume(aid,jit_token=token)
    raise ValueError('Unknown command')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','show','approve','reject','issue','inspect-token','revoke-token','resume'])
    parser.add_argument('--workdir',default='results/local-approval-demo')
    args=parser.parse_args()
    try: result=run(args.command,args.workdir)
    except ValueError as exc: parser.exit(2,str(exc)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
