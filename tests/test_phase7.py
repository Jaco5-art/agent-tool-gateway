import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_phase6
from agent_gateway.policy.jit import JITError, JITStore
from agent_gateway.policy.capabilities import CapabilityStore
from agent_gateway.policy.engine import PolicyBlocked

class JITTests(unittest.TestCase):
    def setUp(self):
        self.f=test_phase6.ApprovalTests();self.f.setUp()
        self.service=self.f.f.service;self.aid=self.f.approved()
    def tearDown(self): self.f.tearDown()
    def test_mint_does_not_execute(self):
        token,p=self.service.issue_jit(self.aid)
        self.assertEqual(self.service.jit.inspect(token)['status'],'active');self.f.no_refunds()
        self.assertLessEqual(p['expires_at']-p['issued_at'],300)
    def test_valid_token_consumed(self):
        token,_=self.service.issue_jit(self.aid);self.service.resume(self.aid,jit_token=token)
        self.assertEqual(self.service.jit.inspect(token)['status'],'consumed')
    def test_token_required_direct_call(self):
        with self.assertRaisesRegex(JITError,'TOKEN_REQUIRED'): self.service.call('refund.issue',test_phase6.REFUND,approval_id=self.aid)
        self.f.no_refunds()
    def test_invalid_token(self):
        with self.assertRaisesRegex(JITError,'UNKNOWN_JIT'): self.service.resume(self.aid,jit_token='forged')
        self.f.no_refunds()
    def test_ttl_rejected(self):
        for ttl in [0,301,True,1.5]:
            with self.assertRaises(JITError): self.service.issue_jit(self.aid,ttl=ttl)
    def test_expiry_boundary(self):
        token,p=self.service.issue_jit(self.aid,ttl=1)
        with patch('agent_gateway.policy.jit.time.time',return_value=p['expires_at']):
            with self.assertRaisesRegex(JITError,'JIT_EXPIRED'): self.service.resume(self.aid,jit_token=token)
        self.f.no_refunds()
    def test_revocation(self):
        token,p=self.service.issue_jit(self.aid);self.service.jit.revoke(p['token_id'])
        with self.assertRaisesRegex(JITError,'NOT_ACTIVE'): self.service.resume(self.aid,jit_token=token)
        self.f.no_refunds()
    def test_no_reissue_after_revocation(self):
        token,p=self.service.issue_jit(self.aid);self.service.jit.revoke(p['token_id'])
        with self.assertRaisesRegex(JITError,'ALREADY_ISSUED'): self.service.issue_jit(self.aid)
    def test_wrong_resource_binding(self):
        token,_=self.service.issue_jit(self.aid)
        with self.assertRaisesRegex(JITError,'PARAMETERS_MISMATCH'):
            self.service.jit.consume(token,self.aid,self.f.f.context,'refund.issue',test_phase6.REFUND|{'order_id':'other'},'phase7-v1')
        self.f.no_refunds()
    def test_wrong_tool_binding(self):
        token,_=self.service.issue_jit(self.aid)
        with self.assertRaisesRegex(JITError,'PARAMETERS_MISMATCH'):
            self.service.jit.consume(token,self.aid,self.f.f.context,'ticket.close',test_phase6.REFUND,'phase7-v1')
    def test_wrong_agent_binding(self):
        token,_=self.service.issue_jit(self.aid)
        with self.assertRaisesRegex(JITError,'CONTEXT_MISMATCH'):
            self.service.jit.consume(token,self.aid,self.f.f.context.model_copy(update={'agent_id':'other'}),'refund.issue',test_phase6.REFUND,'phase7-v1')
    def test_wrong_approval(self):
        token,_=self.service.issue_jit(self.aid);other=self.f.approved()
        with self.assertRaisesRegex(JITError,'APPROVAL_MISMATCH'): self.service.resume(other,jit_token=token)
    def test_amount_change(self):
        token,_=self.service.issue_jit(self.aid)
        with self.assertRaisesRegex(JITError,'PARAMETERS_MISMATCH'): self.service.call('refund.issue',test_phase6.REFUND|{'amount_minor':1300},approval_id=self.aid,jit_token=token)
        self.assertEqual(self.service.jit.inspect(token)['status'],'active');self.f.no_refunds()
    def test_changed_capability(self):
        token,_=self.service.issue_jit(self.aid);CapabilityStore(self.f.f.registry.path).revoke('user_a_refund_issue')
        with self.assertRaises(PolicyBlocked): self.service.resume(self.aid,jit_token=token)
        self.f.no_refunds()
    def test_replay(self):
        token,_=self.service.issue_jit(self.aid);self.service.resume(self.aid,jit_token=token)
        with self.assertRaisesRegex(JITError,'NOT_ACTIVE'): self.service.resume(self.aid,jit_token=token)
    def test_concurrent_same_token_once(self):
        token,_=self.service.issue_jit(self.aid)
        def execute(_):
            try: self.service.resume(self.aid,jit_token=token);return True
            except JITError: return False
        with ThreadPoolExecutor(max_workers=2) as pool: self.assertEqual(sum(pool.map(execute,range(2))),1)
        with self.f.f.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],1)
    def test_plaintext_token_not_stored_or_audited(self):
        token,_=self.service.issue_jit(self.aid);self.service.resume(self.aid,jit_token=token)
        with self.service.jit.registry.connect() as db:
            row=dict(db.execute('SELECT * FROM execution_grants').fetchone())
            self.assertNotIn(token,json.dumps(row))
        self.assertNotIn(token,(self.f.f.root/'audit.jsonl').read_text())
    def test_persistence(self):
        token,p=self.service.issue_jit(self.aid)
        self.assertEqual(JITStore(self.f.f.registry.path).inspect(token)['token_id'],p['token_id'])
    def test_failed_approval_claim_rolls_back_token(self):
        token,_=self.service.issue_jit(self.aid)
        with self.service.jit.registry.connect() as db: db.execute("UPDATE reviewers SET active=0 WHERE id='reviewer_a'")
        with self.assertRaises(ValueError): self.service.resume(self.aid,jit_token=token)
        self.assertEqual(self.service.jit.inspect(token)['status'],'active')
        self.assertEqual(self.service.approvals.get(self.aid)['status'],'approved')
    def test_uncertain_execution_keeps_token_consumed(self):
        token,_=self.service.issue_jit(self.aid)
        with patch.object(self.f.f.backend,'execute',side_effect=RuntimeError('unknown')):
            with self.assertRaises(RuntimeError): self.service.resume(self.aid,jit_token=token)
        self.assertEqual(self.service.jit.inspect(token)['status'],'consumed')
        self.assertEqual(self.service.approvals.get(self.aid)['status'],'execution_uncertain')
