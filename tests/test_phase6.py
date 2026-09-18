import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_phase2
from agent_gateway.approval.store import ApprovalStore, ApprovalError
from agent_gateway.policy.engine import PolicyBlocked
from agent_gateway.policy.capabilities import CapabilityStore
from agent_gateway.service import Service
from agent_gateway.client import connect

REFUND=dict(order_id='order_438',amount_minor=1200,currency='GBP',reason='Demo')

class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.f=test_phase2.IdentityTests();self.f.setUp()
        self.store=self.f.service.approvals
        self.reviewer=self.store.register_reviewer('reviewer_a','demo-tenant',['refund.issue'],['order:order_438'],time.time()+600)
    def tearDown(self): self.f.tearDown()
    def pending(self):
        with self.assertRaises(PolicyBlocked) as cm: self.f.service.call('refund.issue',REFUND)
        return cm.exception.decision.approval_id
    def approved(self):
        aid=self.pending();self.store.decide(aid,self.reviewer,'approve');return aid
    def no_refunds(self):
        with self.f.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)
    def test_pending_persisted(self):
        aid=self.pending();record=ApprovalStore(self.f.registry.path).get(aid)
        self.assertEqual(record['status'],'pending');self.assertEqual(record['parameters'],REFUND);self.no_refunds()
    def test_pending_cannot_execute(self):
        with self.assertRaises(ApprovalError): self.f.service.resume(self.pending())
        self.no_refunds()
    def test_approve_then_resume(self):
        aid=self.approved();self.no_refunds()
        result=self.f.service.resume(aid)
        self.assertEqual(result['amount_minor'],1200);self.assertEqual(self.store.get(aid)['status'],'executed')
    def test_rejection(self):
        aid=self.pending();self.store.decide(aid,self.reviewer,'reject')
        with self.assertRaises(ApprovalError): self.f.service.resume(aid)
        self.no_refunds()
    def test_invalid_reviewer_token(self):
        with self.assertRaises(ApprovalError): self.store.decide(self.pending(),'forged','approve')
        self.no_refunds()
    def test_requester_token_is_not_reviewer(self):
        with self.assertRaises(ApprovalError): self.store.decide(self.pending(),self.f.token,'approve')
    def test_reviewer_wrong_tenant(self):
        token=self.store.register_reviewer('foreign','other',['refund.issue'],['order:order_438'],time.time()+600)
        with self.assertRaises(ApprovalError): self.store.decide(self.pending(),token,'approve')
    def test_reviewer_wrong_scope(self):
        token=self.store.register_reviewer('limited','demo-tenant',['ticket.close'],['ticket:ticket_101'],time.time()+600)
        with self.assertRaises(ApprovalError): self.store.decide(self.pending(),token,'approve')
    def test_self_approval_forbidden(self):
        token=self.store.register_reviewer('user_a','demo-tenant',['refund.issue'],['order:order_438'],time.time()+600)
        with self.assertRaisesRegex(ApprovalError,'SELF_APPROVAL'): self.store.decide(self.pending(),token,'approve')
    def test_parameters_cannot_change(self):
        aid=self.approved();token,_=self.f.service.issue_jit(aid)
        with self.assertRaisesRegex(ApprovalError,'PARAMETERS_MISMATCH'): self.f.service.call('refund.issue',REFUND|{'amount_minor':1300},approval_id=aid,jit_token=token)
        self.no_refunds()
    def test_reason_cannot_change(self):
        aid=self.approved();token,_=self.f.service.issue_jit(aid)
        with self.assertRaisesRegex(ApprovalError,'PARAMETERS_MISMATCH'): self.f.service.call('refund.issue',REFUND|{'reason':'Different'},approval_id=aid,jit_token=token)
    def test_session_cannot_change(self):
        aid=self.approved();context=self.f.context.model_copy(update={'session_id':'other'})
        token=self.f.registry.issue_session(context)
        service=Service(self.f.backend,self.f.root/'other.jsonl','other',registry=self.f.registry,token=token)
        with self.assertRaisesRegex(ApprovalError,'CONTEXT_MISMATCH'): service.resume(aid)
    def test_double_resume_blocked(self):
        aid=self.approved();self.f.service.resume(aid)
        with self.assertRaises(ApprovalError): self.f.service.resume(aid)
        with self.f.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],1)
    def test_concurrent_resume_once(self):
        aid=self.approved()
        def run(_):
            try: self.f.service.resume(aid);return True
            except ApprovalError: return False
        with ThreadPoolExecutor(max_workers=2) as pool: self.assertEqual(sum(pool.map(run,range(2))),1)
        with self.f.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],1)
    def test_cannot_reverse_rejection(self):
        aid=self.pending();self.store.decide(aid,self.reviewer,'reject')
        with self.assertRaises(ApprovalError): self.store.decide(aid,self.reviewer,'approve')
    def test_current_capability_rechecked(self):
        aid=self.approved();CapabilityStore(self.f.registry.path).revoke('user_a_refund_issue')
        with self.assertRaises(PolicyBlocked): self.f.service.resume(aid)
        self.no_refunds()
    def test_current_identity_rechecked(self):
        aid=self.approved();self.f.registry.revoke('session_a')
        with self.assertRaises(ValueError): self.f.service.resume(aid)
        self.no_refunds()
    def test_reviewer_disabled_after_approval(self):
        aid=self.approved()
        with self.store.registry.connect() as db: db.execute("UPDATE reviewers SET active=0 WHERE id='reviewer_a'")
        with self.assertRaises(ApprovalError): self.f.service.resume(aid)
        self.no_refunds()
    def test_expired_approval(self):
        aid=self.approved();future=self.store.get(aid)['expires_at']
        with patch('agent_gateway.approval.store.time.time',return_value=future):
            self.assertEqual(self.store.get(aid)['status'],'expired')
            with self.assertRaisesRegex(ApprovalError,'EXPIRED'): self.f.service.resume(aid)
    def test_pending_expiry_prevents_review(self):
        aid=self.pending();future=self.store.get(aid)['expires_at']
        with patch('agent_gateway.approval.store.time.time',return_value=future):
            with self.assertRaisesRegex(ApprovalError,'EXPIRED'): self.store.decide(aid,self.reviewer,'approve')
        self.no_refunds()
    def test_reviewer_expiry_on_resume(self):
        aid=self.approved()
        with self.store.registry.connect() as db: db.execute("UPDATE reviewers SET expires=0 WHERE id='reviewer_a'")
        with self.assertRaisesRegex(ApprovalError,'REVIEWER_NOT_AUTHORIZED'): self.f.service.resume(aid)
        self.no_refunds()
    def test_business_state_rechecked_on_resume(self):
        aid=self.approved()
        with self.f.backend.connect() as db: db.execute('UPDATE orders SET refunded_minor=99999')
        with self.assertRaisesRegex(ValueError,'REFUND_EXCEEDS_BALANCE'): self.f.service.resume(aid)
        self.no_refunds()
    def test_unknown_approval(self):
        with self.assertRaisesRegex(ApprovalError,'UNKNOWN_APPROVAL'): self.f.service.resume('missing')
    def test_policy_version_change(self):
        aid=self.approved()
        with self.store.registry.connect() as db:
            row=self.store.row(db,aid);p=json.loads(row['payload']);p['policy_version']='old'
            db.execute('UPDATE approvals SET payload=? WHERE id=?',(json.dumps(p),aid))
        with self.assertRaisesRegex(ApprovalError,'POLICY_CHANGED'): self.f.service.resume(aid)
    def test_denial_creates_no_approval(self):
        CapabilityStore(self.f.registry.path).revoke('user_a_refund_issue')
        with self.assertRaises(PolicyBlocked): self.f.service.call('refund.issue',REFUND)
        with self.store.registry.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM approvals').fetchone()[0],0)
    def test_uncertain_execution_never_auto_retried(self):
        aid=self.approved()
        with patch.object(self.f.backend,'execute',side_effect=RuntimeError('unknown outcome')):
            with self.assertRaises(RuntimeError): self.f.service.resume(aid)
        self.assertEqual(self.store.get(aid)['status'],'execution_uncertain')
        with self.assertRaises(ApprovalError): self.f.service.resume(aid)
    def test_audit_links_approval_and_execution(self):
        aid=self.approved();self.f.service.resume(aid)
        rows=[json.loads(x) for x in (self.f.root/'audit.jsonl').read_text().splitlines()]
        self.assertTrue(all(r['approval_id']==aid for r in rows))
        self.assertEqual([r['policy_decision'] for r in rows],['REQUIRE_APPROVAL','ALLOW'])
        self.assertNotIn(self.reviewer,(self.f.root/'audit.jsonl').read_text())

class ApprovalMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_request_review_and_gateway_resume(self):
        f=test_phase2.IdentityTests();f.setUp()
        try:
            async with connect(f.root/'business.sqlite',f.root/'mcp.jsonl','session_a',registry=f.registry,token=f.token) as session:
                result=await session.call_tool('refund.issue',REFUND)
                body=json.loads(result.content[0].text);aid=body['approval_id']
                self.assertEqual(body['decision'],'REQUIRE_APPROVAL')
                token=f.service.approvals.register_reviewer('reviewer_a','demo-tenant',['refund.issue'],['order:order_438'],time.time()+600)
                f.service.approvals.decide(aid,token,'approve')
                resumed=f.service.resume(aid)
                self.assertEqual(resumed['amount_minor'],1200)
            with f.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],1)
        finally: f.tearDown()
