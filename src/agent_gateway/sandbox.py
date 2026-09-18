"""Trusted Docker control plane. Never executes worker code on the host.

Only administrators choose images/code. Model tool parameters cannot change them.
"""
import json
import re
import shutil
import subprocess
import tempfile
import threading
from uuid import uuid4
from .contracts import QueryOutput

class SandboxError(ValueError):
    pass


def bounded_process(command, *, payload=b'', timeout=20, output_limit=131072):
    """Bound stdout + stderr in memory; kill CLI on timeout or output overflow."""
    with tempfile.TemporaryFile() as source:
        source.write(payload); source.seek(0)
        try:
            proc=subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        except OSError as exc:
            raise SandboxError('SANDBOX_RUNTIME_UNAVAILABLE') from exc
        buffers=[bytearray(),bytearray()]
        lock=threading.Lock(); overflow=threading.Event()
        def drain(pipe,index):
            try:
                while True:
                    chunk=pipe.read(4096)
                    if not chunk: break
                    with lock:
                        room=output_limit-sum(map(len,buffers))
                        buffers[index].extend(chunk[:max(0,room)])
                        if len(chunk)>room:
                            overflow.set(); proc.kill()
            finally:
                pipe.close()
        readers=[threading.Thread(target=drain,args=(pipe,i),daemon=True) for i,pipe in enumerate((proc.stdout,proc.stderr))]
        for thread in readers: thread.start()
        timed_out=False
        try: proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True; proc.kill(); proc.wait()
        for thread in readers: thread.join(timeout=3)
        if any(t.is_alive() for t in readers): raise SandboxError('SANDBOX_PIPE_NOT_CLOSED')
        if timed_out: raise SandboxError('SANDBOX_TIMEOUT')
        if overflow.is_set(): raise SandboxError('SANDBOX_OUTPUT_LIMIT')
        return proc.returncode,bytes(buffers[0]),bytes(buffers[1])


class DockerSandbox:
    def __init__(self, image='python:3.12-slim', *, timeout=15):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:/@-]{0,255}',image):
            raise SandboxError('SANDBOX_INVALID_IMAGE')
        if not 1<=timeout<=30: raise SandboxError('SANDBOX_INVALID_TIMEOUT')
        self.image=image; self.timeout=timeout
        self.docker=shutil.which('docker')
        self.image_id=None

    def prepare(self):
        if not self.docker: raise SandboxError('SANDBOX_DOCKER_NOT_FOUND')
        code,out,_=bounded_process([self.docker,'image','inspect',self.image,'--format','{{.Id}}|{{.Os}}|{{json .Config.Volumes}}'])
        if code: raise SandboxError('SANDBOX_IMAGE_OR_DAEMON_UNAVAILABLE')
        parts=out.decode().strip().split('|')
        if len(parts)!=3 or not re.fullmatch(r'sha256:[0-9a-f]{64}',parts[0]) or parts[1]!='linux' or parts[2] not in ('null','{}'):
            raise SandboxError('SANDBOX_UNSUPPORTED_IMAGE')
        # Use immutable local image ID, never pull implicitly or race a mutable tag.
        self.image_id=parts[0]

    def create_command(self,name,code):
        return [self.docker,'create','--pull=never','--name',name,'--label','agent-gateway.phase=9',
                '--network=none','--read-only','--user=65534:65534','--cap-drop=ALL',
                '--security-opt=no-new-privileges:true','--memory=128m','--memory-swap=128m',
                '--cpus=0.5','--pids-limit=32','--ulimit','nofile=64:64',
                '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777',
                '--workdir=/tmp','--log-driver=none','--no-healthcheck',
                '--entrypoint=python','-i',self.image_id,'-I','-B','-c',code]

    def run(self,code,payload=None):
        data=json.dumps(payload).encode()
        if len(data)>65536: raise SandboxError('SANDBOX_INPUT_LIMIT')
        if self.image_id is None: self.prepare()
        name='agent-gateway-'+uuid4().hex
        created=False
        try:
            status,_,_=bounded_process(self.create_command(name,code))
            if status: raise SandboxError('SANDBOX_CREATE_FAILED')
            created=True
            status,out,_=bounded_process([self.docker,'start','-a','-i',name],payload=data,timeout=self.timeout)
            if status: raise SandboxError('SANDBOX_WORKER_FAILED')
            try: return json.loads(out)
            except (ValueError,UnicodeError) as exc: raise SandboxError('SANDBOX_INVALID_OUTPUT') from exc
        finally:
            # Removing the daemon-side container is essential: killing the CLI alone is insufficient.
            status,_,_=bounded_process([self.docker,'rm','-f','-v',name])
            if status and created: raise SandboxError('SANDBOX_CLEANUP_FAILED')

QUERY_WORKER='''import json,sqlite3,sys
p=json.load(sys.stdin)
db=sqlite3.connect(':memory:')
db.row_factory=sqlite3.Row
db.execute('CREATE TABLE orders(order_id TEXT,customer_id TEXT,amount_minor INTEGER,refunded_minor INTEGER,currency TEXT)')
columns=['order_id','customer_id','amount_minor','refunded_minor','currency']
db.executemany('INSERT INTO orders VALUES (?,?,?,?,?)',[[r[k] for k in columns] for r in p['rows']])
rows=[dict(r) for r in db.execute('SELECT * FROM orders WHERE customer_id=? ORDER BY order_id LIMIT ?',(p['customer_id'],p['limit']))]
print(json.dumps(dict(columns=columns,rows=rows,row_count=len(rows))))
db.close()
'''

class SandboxedQueryBackend:
    """Scope data on host after policy; execute the fixed query in isolated memory."""
    def __init__(self,backend,sandbox):
        self.backend=backend; self.sandbox=sandbox
    def __getattr__(self,name): return getattr(self.backend,name)
    def execution_metadata(self,tool):
        return {'execution_environment':'docker' if tool=='database.query' else 'host',
                'sandbox_profile':'query-v1' if tool=='database.query' else None}
    def execute(self,tool,p):
        if tool!='database.query': return self.backend.execute(tool,p)
        # Fail before materializing data if Docker/image is unavailable.
        if self.sandbox.image_id is None: self.sandbox.prepare()
        with self.backend.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM orders WHERE customer_id=? ORDER BY order_id LIMIT ?', (p['customer_id'],p['limit']))]
        result=QueryOutput.model_validate(self.sandbox.run(QUERY_WORKER,dict(rows=rows,customer_id=p['customer_id'],limit=p['limit']))).model_dump()
        # Worker output is untrusted; exact fixed-query consistency required.
        if result['rows']!=rows or result['row_count']!=len(rows) or result['columns']!=['order_id','customer_id','amount_minor','refunded_minor','currency']:
            raise SandboxError('SANDBOX_RESULT_MISMATCH')
        return result
