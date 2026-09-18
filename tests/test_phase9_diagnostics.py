import os
import unittest
from unittest.mock import patch,Mock
from agent_gateway.analysis.e2b_runner import E2BRunner,AnalysisError,safe_diagnostic

class DiagnosticsTests(unittest.TestCase):
    def test_redaction(self):
        with patch.dict(os.environ,{'E2B_API_KEY':'literal-secret'}):
            d=safe_diagnostic(RuntimeError('literal-secret e2b_othersecret sk-othersecret https://host/?token=secret Authorization: Bearer abc token=def'),'create_sandbox')
        for secret in ['literal-secret','e2b_othersecret','sk-othersecret','https://','abc','def']: self.assertNotIn(secret,d['message'])
    def test_create_stage_preserved(self):
        with self.assertRaises(AnalysisError) as ctx:
            E2BRunner(factory=Mock(side_effect=ValueError('invalid CIDR'))).run('',[])
        self.assertEqual(ctx.exception.diagnostic['stage'],'create_sandbox')
        self.assertEqual(str(ctx.exception),'E2B_EXECUTION_FAILED')
    def test_upload_failure_stage_and_cleanup(self):
        sandbox=Mock();sandbox.files.write.side_effect=ValueError('file failure')
        with self.assertRaises(AnalysisError) as ctx: E2BRunner(factory=Mock(return_value=sandbox)).run('',[])
        self.assertEqual(ctx.exception.diagnostic['stage'],'upload_data')
        sandbox.kill.assert_called_once()
