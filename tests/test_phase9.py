import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from agent_gateway.sandbox import DockerSandbox, SandboxedQueryBackend, SandboxError, bounded_process
from agent_gateway.backend import Backend
from agent_gateway.service import Service
from agent_gateway.identity import local_session

class SupervisorTests(unittest.TestCase):
    def test_bounded_output(self):
        with self.assertRaisesRegex(SandboxError,'OUTPUT_LIMIT'):
            bounded_process([sys.executable,'-c','print("x"*20000)'],output_limit=1000)
    def test_timeout(self):
        with self.assertRaisesRegex(SandboxError,'TIMEOUT'):
            bounded_process([sys.executable,'-c','import time;time.sleep(10)'],timeout=.1)
    def test_separate_stderr(self):
        status,out,err=bounded_process([sys.executable,'-c','import sys;print("ok");print("error",file=sys.stderr)'])
        self.assertEqual(status,0); self.assertIn(b'ok',out); self.assertIn(b'error',err)
    def test_missing_runtime(self):
        s=DockerSandbox();s.docker=None
        with self.assertRaisesRegex(SandboxError,'DOCKER_NOT_FOUND'): s.run('print(1)')
    def test_image_argument_injection(self):
        for image in ['--privileged','python;whoami','python image','']:
            with self.assertRaises(SandboxError): DockerSandbox(image)
    def test_create_constraints(self):
        s=DockerSandbox();s.image_id='sha256:'+'a'*64
        command=s.create_command('test','print(1)')
        for flag in ['--network=none','--read-only','--cap-drop=ALL','--user=65534:65534','--memory=128m','--pids-limit=32','--log-driver=none']:
            self.assertIn(flag,command)
        for flag in ['--privileged','--volume','--mount','--env','--pid=host']:
            self.assertNotIn(flag,command)
    def test_input_limit(self):
        with self.assertRaisesRegex(SandboxError,'INPUT_LIMIT'): DockerSandbox().run('', 'x'*70000)
    @patch('agent_gateway.sandbox.bounded_process')
    def test_timeout_removes_container(self,run):
        s=DockerSandbox();s.image_id='sha256:'+'a'*64;s.docker='docker'
        run.side_effect=[(0,b'id',b''),SandboxError('SANDBOX_TIMEOUT'),(0,b'',b'')]
        with self.assertRaisesRegex(SandboxError,'TIMEOUT'): s.run('')
        self.assertEqual(run.call_args.args[0][1:4],['rm','-f','-v'])
    @patch('agent_gateway.sandbox.bounded_process')
    def test_cleanup_failure_not_success(self,run):
        s=DockerSandbox();s.image_id='sha256:'+'a'*64;s.docker='docker'
        run.side_effect=[(0,b'id',b''),(0,b'{}',b''),(1,b'',b'error')]
        with self.assertRaisesRegex(SandboxError,'CLEANUP_FAILED'): s.run('')
    @patch('agent_gateway.sandbox.bounded_process')
    def test_no_implicit_pull(self,run):
        s=DockerSandbox();s.docker='docker';run.return_value=(1,b'',b'')
        with self.assertRaisesRegex(SandboxError,'IMAGE_OR_DAEMON_UNAVAILABLE'): s.run('')
        self.assertEqual(run.call_count,1)
    @patch('agent_gateway.sandbox.bounded_process')
    def test_reject_image_volumes(self,run):
        s=DockerSandbox();s.docker='docker';run.return_value=(0,('sha256:'+'a'*64+'|linux|{"/data":{}}').encode(),b'')
        with self.assertRaisesRegex(SandboxError,'UNSUPPORTED_IMAGE'): s.prepare()

class QueryRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.backend=Backend(self.root/'business.sqlite')
        self.runner=Mock(image_id='sha256:test')
        self.wrapper=SandboxedQueryBackend(self.backend,self.runner)
        registry,token=local_session(self.root/'identity.sqlite','test')
        self.service=Service(self.wrapper,self.root/'audit.jsonl','test',registry=registry,token=token)
        self.p=dict(query_id='orders_by_customer',customer_id='customer_001',limit=10)
    def tearDown(self): self.temp.cleanup()
    def test_query_scoped_input_and_audit(self):
        with self.backend.connect() as db:
            db.execute("INSERT INTO orders VALUES ('foreign','other',100,0,'GBP')")
        self.runner.run.return_value=self.backend.execute('database.query',self.p)
        result=self.service.call('database.query',self.p)
        payload=self.runner.run.call_args.args[1]
        self.assertEqual(len(payload['rows']),1)
        self.assertEqual(payload['rows'][0]['customer_id'],'customer_001')
        self.assertEqual(result['row_count'],1)
        self.assertEqual(json.loads((self.root/'audit.jsonl').read_text())['execution_environment'],'docker')
    def test_policy_denial_before_sandbox(self):
        self.service.registry.revoke(self.service.session_id)
        with self.assertRaises(ValueError): self.service.call('database.query',self.p)
        self.runner.run.assert_not_called()
    def test_sandbox_failure_no_host_fallback(self):
        self.runner.run.side_effect=SandboxError('SANDBOX_TIMEOUT')
        with self.assertRaisesRegex(SandboxError,'TIMEOUT'): self.service.call('database.query',self.p)
        self.assertEqual(json.loads((self.root/'audit.jsonl').read_text())['execution_status'],'failed')
    def test_tampered_output_rejected(self):
        self.runner.run.return_value=dict(columns=[],rows=[],row_count=0)
        with self.assertRaisesRegex(SandboxError,'RESULT_MISMATCH'): self.service.call('database.query',self.p)
    def test_other_tools_keep_business_semantics(self):
        self.assertEqual(self.service.call('ticket.read',{'ticket_id':'ticket_101'})['status'],'open')
        self.runner.run.assert_not_called()
