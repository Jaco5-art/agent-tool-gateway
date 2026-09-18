import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from pydantic import ValidationError
from agent_gateway.analysis.backend import AnalysisBackend
from agent_gateway.analysis.contracts import AnalysisInput
from agent_gateway.analysis.e2b_runner import E2BRunner,AnalysisError
from agent_gateway.analysis.fixtures import analysis_session,seed_analysis,REFERENCE_CODE,EXPECTED
from agent_gateway.backend import Backend
from agent_gateway.service import Service
from agent_gateway.binding.manifest import visible_tools
from agent_gateway.policy.capabilities import CapabilityStore
from agent_gateway.policy.engine import PolicyBlocked
from agent_gateway.identity import local_session
from agent_gateway.client import connect

class AnalysisPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.backend=Backend(self.root/'business.sqlite');seed_analysis(self.backend)
        self.runner=Mock();self.runner.run.return_value=dict(result=EXPECTED,sandbox_id='test-only',provider='e2b',code_sha256='hash',dataset_sha256='hash',rows_supplied=4)
        self.registry,self.token=analysis_session(self.root/'identity.sqlite','analysis')
        self.service=Service(AnalysisBackend(self.backend,self.runner),self.root/'audit.jsonl','analysis',registry=self.registry,token=self.token)
        self.p=dict(customer_id='customer_001',limit=100,code=REFERENCE_CODE)
    def tearDown(self): self.tmp.cleanup()
    def test_scoped_data_and_provenance(self):
        result=self.service.call('analysis.run',self.p)
        rows=self.runner.run.call_args.args[1]
        self.assertEqual(len(rows),4);self.assertEqual({r['customer_id'] for r in rows},{'customer_001'})
        self.assertEqual(result['result'],EXPECTED)
        audit=json.loads((self.root/'audit.jsonl').read_text());self.assertEqual(audit['sandbox_id'],'test-only')
        self.assertNotIn('code',audit);self.assertNotIn('result',audit)
    def test_foreign_tenant_denied_before_upload(self):
        with self.assertRaises(PolicyBlocked): self.service.call('analysis.run',dict(self.p,customer_id='customer_002'))
        self.runner.run.assert_not_called()
    def test_user_revocation_denied(self):
        c=self.registry.resolve(self.token);CapabilityStore(self.registry.path).revoke(c.user_id+'_analysis')
        with self.assertRaises(PolicyBlocked): self.service.call('analysis.run',self.p)
        self.runner.run.assert_not_called();self.assertEqual(visible_tools(self.service),[])
    def test_agent_disabled_denied(self):
        c=self.registry.resolve(self.token);self.registry.set_status(c.agent_id,'disabled')
        with self.assertRaises(ValueError): self.service.call('analysis.run',self.p)
        self.runner.run.assert_not_called()
    def test_task_scope_denied(self):
        c=self.registry.resolve(self.token).model_copy(update={'session_id':'limited','task_scope':()})
        t=self.registry.issue_session(c)
        service=Service(self.service.backend,self.root/'limited.jsonl','limited',registry=self.registry,token=t)
        with self.assertRaises(PolicyBlocked): service.call('analysis.run',self.p)
        self.runner.run.assert_not_called()
    def test_broken_delegation_denied(self):
        c=self.registry.resolve(self.token).model_copy(update={'session_id':'delegated','delegation_leaf_id':'missing'})
        t=self.registry.issue_session(c)
        service=Service(self.service.backend,self.root/'delegated.jsonl','delegated',registry=self.registry,token=t)
        with self.assertRaises(PolicyBlocked): service.call('analysis.run',self.p)
        self.runner.run.assert_not_called()
    def test_explicit_tool_visibility(self): self.assertEqual(visible_tools(self.service),['analysis.run'])
    def test_old_query_grants_do_not_allow_code(self):
        r,t=local_session(self.root/'old.sqlite','old')
        s=Service(self.service.backend,self.root/'old.jsonl','old',registry=r,token=t)
        self.assertNotIn('analysis.run',visible_tools(s))
        with self.assertRaises(PolicyBlocked): s.call('analysis.run',self.p)
        self.runner.run.assert_not_called()
    def test_code_and_identity_fields_strict(self):
        for patcher in [{'code':'x'*12001},{'limit':101},{'limit':True},{'user_id':'admin'},{'network':True}]:
            with self.subTest(patcher=patcher),self.assertRaises(ValidationError): self.service.call('analysis.run',dict(self.p,**patcher))
        self.runner.run.assert_not_called()
    def test_limit_applied_before_upload(self):
        self.service.call('analysis.run',dict(self.p,limit=1))
        self.assertEqual(len(self.runner.run.call_args.args[1]),1)
    def test_runner_failure_no_fallback(self):
        self.runner.run.side_effect=AnalysisError('ANALYSIS_TIMEOUT')
        with self.assertRaisesRegex(AnalysisError,'TIMEOUT'): self.service.call('analysis.run',self.p)
        self.assertEqual(self.backend.execute('ticket.read',{'ticket_id':'ticket_101'})['status'],'open')

class E2BAdapterTests(unittest.TestCase):
    def sandbox(self,reply=None):
        sandbox=Mock(sandbox_id='fixture-sandbox')
        sandbox.commands.run.return_value=SimpleNamespace(stdout=json.dumps(reply or {'ok':True,'result':{'value':1}}))
        return sandbox
    def test_secret_not_uploaded_and_network_restricted(self):
        sandbox=self.sandbox();factory=Mock(return_value=sandbox)
        code='print({})'
        with patch.dict(os.environ,{'E2B_API_KEY':'test-secret','OPENAI_API_KEY':'model-secret'}):
            r=E2BRunner(factory=factory).run(code,[])
        args=factory.call_args.kwargs
        self.assertEqual(args['network']['deny_out'],['0.0.0.0/0']);self.assertFalse(args['allow_internet_access']);self.assertFalse(args['network']['allow_public_traffic'])
        self.assertEqual(args['envs'],{});self.assertEqual(args['timeout'],60)
        self.assertNotIn('test-secret',str(sandbox.files.write.call_args_list))
        self.assertNotIn('model-secret',str(sandbox.files.write.call_args_list))
        sandbox.kill.assert_called_once();self.assertEqual(r['provider'],'e2b')
    def test_code_not_interpolated_into_command(self):
        sandbox=self.sandbox();code='; touch /tmp/SHOULD_NOT_BE_A_COMMAND'
        E2BRunner(factory=Mock(return_value=sandbox)).run(code,[])
        self.assertNotIn(code,str(sandbox.commands.run.call_args_list))
    def test_timeout_still_cleans(self):
        sandbox=self.sandbox({'ok':False,'reason':'ANALYSIS_TIMEOUT'})
        with self.assertRaisesRegex(AnalysisError,'TIMEOUT'): E2BRunner(factory=Mock(return_value=sandbox)).run('',[])
        sandbox.kill.assert_called_once()
    def test_provider_error_redacted_and_cleaned(self):
        sandbox=self.sandbox();sandbox.files.write.side_effect=RuntimeError('secret-url')
        with self.assertRaisesRegex(AnalysisError,'^E2B_EXECUTION_FAILED$'): E2BRunner(factory=Mock(return_value=sandbox)).run('',[])
        sandbox.kill.assert_called_once()
    def test_cleanup_failure_not_success(self):
        sandbox=self.sandbox();sandbox.kill.side_effect=RuntimeError('down')
        with self.assertRaisesRegex(AnalysisError,'CLEANUP_FAILED'): E2BRunner(factory=Mock(return_value=sandbox)).run('',[])
    def test_missing_key_fails_closed(self):
        with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(AnalysisError,'KEY_MISSING'): E2BRunner().run('',[])
    def test_data_limit_precedes_create(self):
        factory=Mock()
        with self.assertRaisesRegex(AnalysisError,'DATA_LIMIT'): E2BRunner(factory=factory).run('',[{'x':'a'*65536}])
        factory.assert_not_called()
    def test_create_failure_never_runs_code(self):
        with self.assertRaisesRegex(AnalysisError,'EXECUTION_FAILED'): E2BRunner(factory=Mock(side_effect=RuntimeError('down'))).run('',[])
    def test_sdk_contract(self):
        import inspect
        from e2b import Sandbox
        for name in ['secure','allow_internet_access','network','timeout']: self.assertIn(name,inspect.signature(Sandbox.create).parameters)

class AnalysisMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_mcp_manifest_and_missing_key(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'E2B_API_KEY':''}):
            root=Path(directory)
            async with connect(root/'business.sqlite',root/'audit.jsonl',e2b_analysis=True) as session:
                manifest=await session.list_tools()
                self.assertEqual([t.name for t in manifest.tools],['analysis.run'])
                result=await session.call_tool('analysis.run',dict(customer_id='customer_001',limit=1,code='print({})'))
                self.assertTrue(result.is_error)
                self.assertIn('E2B_API_KEY_MISSING',result.content[0].text)
