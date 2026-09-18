"""Phase 9: regression and real Docker probes are reported separately."""
import argparse
import asyncio
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from .sandbox import DockerSandbox,SandboxError
from .client import connect

PROBE='''import errno,json,os,pathlib,socket
p=json.load(__import__('sys').stdin)
checks={}
checks['non_root']=os.getuid()==65534
status=pathlib.Path('/proc/self/status').read_text()
fields=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
checks['no_capabilities']=int(fields['CapEff'].strip(),16)==0
checks['no_new_privileges']=fields['NoNewPrivs'].strip()=='1'
checks['seccomp']=fields['Seccomp'].strip()=='2'
root=next(line.split() for line in pathlib.Path('/proc/mounts').read_text().splitlines() if line.split()[1]=='/')
checks['root_read_only']='ro' in root[3].split(',')
checks['host_file_hidden']=not pathlib.Path(p['sentinel']).exists()
checks['credentials_absent']=not any(k in os.environ for k in ('OPENAI_API_KEY','GATEWAY_SESSION_TOKEN'))
checks['docker_socket_absent']=not pathlib.Path('/var/run/docker.sock').exists()
pathlib.Path('/tmp/probe').write_text('temporary')
checks['tmp_writable']=pathlib.Path('/tmp/probe').read_text()=='temporary'
try:
 with open('/tmp/full','wb') as f: f.write(b'x'*(17*1024*1024))
 checks['tmp_limit']=False
except OSError as e: checks['tmp_limit']=e.errno==errno.ENOSPC
s=socket.socket();s.settimeout(1)
try: s.connect(('192.0.2.1',80));checks['network_blocked']=False
except OSError as e: checks['network_blocked']=e.errno in (errno.ENETUNREACH,errno.EHOSTUNREACH)
finally: s.close()
# Support both common cgroup layouts; unsupported layouts fail visibly.
cg=pathlib.Path('/sys/fs/cgroup')
def read(v2,v1):
 a=cg/v2
 return a.read_text().strip() if a.exists() else (cg/v1).read_text().strip()
checks['memory_limit']=int(read('memory.max','memory/memory.limit_in_bytes'))==134217728
checks['pid_limit']=int(read('pids.max','pids/pids.max'))==32
if (cg/'cpu.max').exists(): quota,period=(cg/'cpu.max').read_text().split()
else: quota=read('cpu.max','cpu/cpu.cfs_quota_us');period=read('cpu.max','cpu/cpu.cfs_period_us')
checks['cpu_limit']=int(quota)/int(period)==0.5
print(json.dumps(checks))
'''

async def mcp_query(root,image):
    async with connect(root/'business.sqlite',root/'audit.jsonl',sandbox_query=True,sandbox_image=image) as session:
        result=await session.call_tool('database.query',{'query_id':'orders_by_customer','customer_id':'customer_001','limit':10})
        if result.is_error: raise SandboxError('SANDBOX_MCP_QUERY_FAILED')
        # Validate the JSON body too, including normal business result.
        body=json.loads(result.content[0].text)
        if body['row_count']!=1 or body['rows'][0]['order_id']!='order_438':
            raise SandboxError('SANDBOX_MCP_QUERY_MISMATCH')
    event=json.loads((root/'audit.jsonl').read_text().splitlines()[-1])
    if event.get('execution_environment')!='docker': raise SandboxError('SANDBOX_AUDIT_MISSING')


def live_check(image):
    runner=DockerSandbox(image)
    report={'status':'blocked','image':image,'checks':{},'error':None}
    try: runner.prepare()
    except SandboxError as exc:
        report['error']=str(exc); return report
    report['image_id']=runner.image_id
    try:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);sentinel=root/'host-only-secret.txt';sentinel.write_text('sentinel')
            report['checks']=runner.run(PROBE,{'sentinel':str(sentinel)})
            asyncio.run(mcp_query(root,runner.image_id))
            report['checks']['real_mcp_query']=True
        for name,code,expected in [
            ('timeout','import time;time.sleep(60)','SANDBOX_TIMEOUT'),
            ('output_limit','import sys;sys.stdout.write("x"*2000000);sys.stdout.flush()','SANDBOX_OUTPUT_LIMIT')]:
            try:
                runner.run(code)
                report['checks'][name]=False
            except SandboxError as exc:
                report['checks'][name]=str(exc)==expected
        report['status']='passed' if all(report['checks'].values()) else 'failed'
    except Exception as exc:
        report['status']='failed';report['error']=str(exc)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='results/local-phase9-check.json')
    parser.add_argument('--image',default='python:3.12-slim')
    args=parser.parse_args()
    tests=Path(__file__).resolve().parents[2]/'tests'
    if not tests.exists(): parser.error('Use an editable source installation: pip install -e .')
    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.discover(str(tests)))
    report=dict(phase=9,timestamp=datetime.now(timezone.utc).isoformat(),tests_run=result.testsRun,
                failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
                regression_status='passed' if result.wasSuccessful() else 'failed',output=stream.getvalue(),live_model='not_run')
    report['sandbox']=live_check(args.image) if result.wasSuccessful() else {'status':'not_run'}
    report['status']=report['sandbox']['status'] if result.wasSuccessful() else 'failed'
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='output'},ensure_ascii=False))
    raise SystemExit(0 if report['status']=='passed' else 2 if report['status']=='blocked' else 1)

if __name__=='__main__': main()
