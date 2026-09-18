"""Phase 9 upgraded: explicitly separate tests, paid E2B probes, and LLM demo."""
import argparse
import asyncio
from datetime import datetime,timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from dotenv import load_dotenv
from .analysis.e2b_runner import E2BRunner,AnalysisError
from .analysis.fixtures import seed_analysis,REFERENCE_CODE,EXPECTED
from .analysis.demo import model_demo
from .backend import Backend
from .client import connect

PROBE='''import json,os,socket,resource
checks={}
checks['non_root']=os.getuid()==65534
checks['no_credentials']=not any(k in os.environ for k in ('E2B_API_KEY','OPENAI_API_KEY','GATEWAY_SESSION_TOKEN'))
checks['authorized_rows_only']={r['customer_id'] for r in json.load(open('/tmp/gateway/data.json'))}=={'customer_001'}
for name,path in [('supervisor_unreadable','/tmp/gateway/worker.py'),('root_secret_unreadable','/etc/shadow')]:
 try:
  open(path).read();checks[name]=False
 except PermissionError: checks[name]=True
try:
 open('/tmp/gateway/data.json','w');checks['dataset_read_only']=False
except PermissionError: checks['dataset_read_only']=True
# A blocked socket must fail with EPERM, not merely DNS/route failure.
import errno
for name,family in [('network_blocked',socket.AF_INET),('ipv6_blocked',socket.AF_INET6),('unix_socket_blocked',socket.AF_UNIX)]:
 try:
  sock=socket.socket(family,socket.SOCK_STREAM)
  sock.close();checks[name]=False
 except OSError as e: checks[name]=e.errno==errno.EPERM
checks['memory_limit']=resource.getrlimit(resource.RLIMIT_AS)[0]==268435456
checks['process_limit']=resource.getrlimit(resource.RLIMIT_NPROC)[0]==16
checks['file_size_limit']=resource.getrlimit(resource.RLIMIT_FSIZE)[0]==1048576
checks['fd_limit']=resource.getrlimit(resource.RLIMIT_NOFILE)[0]==32
print(json.dumps(checks))
'''

async def live_e2b(root):
    if not os.getenv('E2B_API_KEY'): return {'status':'blocked','reason':'E2B_API_KEY_MISSING'}
    root.mkdir(parents=True,exist_ok=True)
    seed_analysis(Backend(root/'business.sqlite'))
    checks={};evidence=[]
    try:
        async with connect(root/'business.sqlite',root/'audit.jsonl',e2b_analysis=True) as session:
            async def invoke(code,customer='customer_001'):
                reply=await session.call_tool('analysis.run',{'customer_id':customer,'limit':100,'code':code})
                body=json.loads(reply.content[0].text)
                if not reply.is_error:
                    evidence.append({k:v for k,v in body.items() if k!='result'})
                return reply.is_error,body
            error,body=await invoke(REFERENCE_CODE)
            checks['reference_analysis']=not error and body.get('result')==EXPECTED
            if error: return {'status':'failed','checks':checks,'reason':body.get('reason_code','E2B_EXECUTION_FAILED'),'diagnostic':body.get('diagnostic')}
            error,body=await invoke(PROBE)
            checks['isolation_probe_completed']=not error
            if not error: checks.update(body['result'])
            for name,code,expected in [
                ('timeout','import time;time.sleep(60)','ANALYSIS_TIMEOUT'),
                ('output_limit','print("x"*200000)','ANALYSIS_OUTPUT_LIMIT'),
                ('invalid_output','print("not JSON")','ANALYSIS_INVALID_JSON')]:
                error,body=await invoke(code)
                checks[name]=error and body.get('reason_code')==expected
            error,body=await invoke('print({})','customer_002')
            checks['cross_tenant_denied']=error and body.get('reason_code')=='RESOURCE_TENANT_MISMATCH'
        return {'status':'passed' if all(checks.values()) else 'failed','checks':checks,'executions':evidence,
            'audit_events':[json.loads(line) for line in (root/'audit.jsonl').read_text().splitlines()]}
    except Exception as exc: return {'status':'failed','checks':checks,'error_type':type(exc).__name__}


def main():
    load_dotenv()
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true',help='Run paid E2B integration probes (no LLM).')
    parser.add_argument('--live-model',action='store_true',help='Also run a paid model-generated analysis demo; implies --live.')
    parser.add_argument('--output',default='results/local-phase9-e2b-check.json')
    args=parser.parse_args()
    tests=Path(__file__).resolve().parents[2]/'tests'
    if not tests.exists(): parser.error('Install editable source with pip install -e ".[e2b]"')
    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.discover(str(tests)))
    report=dict(phase='9-e2b',timestamp=datetime.now(timezone.utc).isoformat(),tests_run=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        regression_status='passed' if result.wasSuccessful() else 'failed',test_output=stream.getvalue(),
        e2b={'status':'not_run'},live_model={'status':'not_run'})
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        if result.wasSuccessful() and (args.live or args.live_model):
            report['e2b']=asyncio.run(live_e2b(root/'e2b'))
        if args.live_model and report['e2b']['status']=='passed':
            report['live_model']=asyncio.run(model_demo(root/'model'))
    if not result.wasSuccessful(): report['status']='failed'
    elif not (args.live or args.live_model): report['status']='local_passed_live_pending'
    elif report['e2b']['status']!='passed': report['status']=report['e2b']['status']
    elif args.live_model: report['status']=report['live_model']['status']
    else: report['status']='e2b_passed_model_pending'
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('test_output','live_model')},ensure_ascii=False))
    print(json.dumps({'live_model_status':report['live_model']['status'],'output':str(path)}))
    raise SystemExit(1 if report['status']=='failed' else 2 if report['status']=='blocked' else 0)

if __name__=='__main__': main()
