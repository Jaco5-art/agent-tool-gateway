import hashlib
import json
import os
import re
from pathlib import Path

class AnalysisError(ValueError):
    def __init__(self,message,diagnostic=None):
        super().__init__(message)
        self.diagnostic=diagnostic


def safe_diagnostic(exc,stage):
    message=str(exc)
    for name in ('E2B_API_KEY','OPENAI_API_KEY','GATEWAY_SESSION_TOKEN'):
        secret=os.getenv(name)
        if secret: message=message.replace(secret,'[REDACTED]')
    message=re.sub(r'https?://[^\s<>\"\']+', '[URL]',message)
    message=re.sub(r'(?i)(?:e2b_|sk-)[A-Za-z0-9_-]+','[REDACTED]',message)
    message=re.sub(r'(?i)(bearer\s+)[^\s,;]+',r'\1[REDACTED]',message)
    message=re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|authorization|token)[\"\']?\s*[:=]\s*[\"\']?)[^\s,;\"\']+',r'\1[REDACTED]',message)
    info={'stage':stage,'exception_type':type(exc).__name__,'message':message[:1200]}
    status=getattr(getattr(exc,'response',None),'status_code',None)
    if isinstance(status,int): info['http_status']=status
    return info

class E2BRunner:
    def __init__(self, *, factory=None):
        self.factory=factory
    def run(self,code,rows):
        payload=json.dumps(rows,ensure_ascii=False,allow_nan=False)
        if len(payload.encode())>65536: raise AnalysisError('ANALYSIS_DATA_LIMIT')
        if not isinstance(code,str) or len(code)>12000: raise AnalysisError('ANALYSIS_CODE_LIMIT')
        key=os.getenv('E2B_API_KEY')
        if self.factory is None and not key: raise AnalysisError('E2B_API_KEY_MISSING')
        if self.factory is None:
            try: from e2b import Sandbox
            except ImportError as exc: raise AnalysisError('E2B_SDK_MISSING') from exc
            factory=Sandbox.create
        else: factory=self.factory
        sandbox=None
        stage="create_sandbox"
        try:
            sandbox=factory(timeout=60,secure=True,allow_internet_access=False,
                network={'deny_out':['0.0.0.0/0'],'allow_public_traffic':False},
                envs={},api_key=key,request_timeout=20,metadata={'project':'agent-tool-gateway','phase':'9-e2b'})
            # All command strings are trusted literals; code/data go only through file APIs.
            stage='prepare_directory'
            sandbox.commands.run('mkdir -p /tmp/gateway/work && chmod 755 /tmp/gateway && chmod 777 /tmp/gateway/work',user='root',timeout=10)
            stage='upload_data'
            sandbox.files.write('/tmp/gateway/data.json',payload,user='root',request_timeout=10)
            stage='upload_code'
            sandbox.files.write('/tmp/gateway/code.py',code,user='root',request_timeout=10)
            stage='upload_supervisor'
            sandbox.files.write('/tmp/gateway/worker.py',Path(__file__).with_name('worker.py').read_text(encoding='utf-8'),user='root',request_timeout=10)
            stage='set_permissions'
            sandbox.commands.run('chmod 444 /tmp/gateway/data.json /tmp/gateway/code.py && chmod 700 /tmp/gateway/worker.py',user='root',timeout=10)
            stage='run_supervisor'
            reply=sandbox.commands.run('python3 -I -B /tmp/gateway/worker.py',user='root',timeout=20,request_timeout=25)
            if len(reply.stdout.encode())>131072: raise AnalysisError('ANALYSIS_OUTPUT_LIMIT')
            stage='decode_result'
            envelope=json.loads(reply.stdout)
            if not envelope.get('ok'):
                reason=envelope.get('reason')
                allowed={'ANALYSIS_TIMEOUT','ANALYSIS_OUTPUT_LIMIT','ANALYSIS_CODE_FAILED','ANALYSIS_INVALID_JSON'}
                raise AnalysisError(reason if reason in allowed else 'ANALYSIS_WORKER_FAILED')
            return {'result':envelope['result'],'sandbox_id':sandbox.sandbox_id,'provider':'e2b',
                'code_sha256':hashlib.sha256(code.encode()).hexdigest(),
                'dataset_sha256':hashlib.sha256(payload.encode()).hexdigest(),'rows_supplied':len(rows)}
        except AnalysisError: raise
        except Exception as exc:
            # Provider exceptions may contain URLs/tokens; expose only a stable code.
            raise AnalysisError('E2B_EXECUTION_FAILED',safe_diagnostic(exc,stage)) from None
        finally:
            if sandbox is not None:
                try: sandbox.kill(request_timeout=15)
                except Exception as exc: raise AnalysisError('E2B_CLEANUP_FAILED',safe_diagnostic(exc,'cleanup')) from None
