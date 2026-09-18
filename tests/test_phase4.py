import json
import unittest
import test_phase2
from agent_gateway.policy.engine import PolicyBlocked
from agent_gateway.policy.capabilities import CapabilityStore
from agent_gateway.service import Service
from agent_gateway.client import connect

REFUND=dict(order_id='order_438',amount_minor=1200,currency='GBP',reason='Demo')
CLOSE=dict(ticket_id='ticket_101',resolution='Resolved',expected_version=1)

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.f=test_phase2.IdentityTests();self.f.setUp()
        self.store=CapabilityStore(self.f.registry.path)
    def tearDown(self): self.f.tearDown()
    def block(self,tool,p,decision,reason=None):
        with self.assertRaises(PolicyBlocked) as cm: self.f.service.call(tool,p)
        self.assertEqual(cm.exception.decision.decision,decision)
        if reason: self.assertEqual(cm.exception.decision.reason_code,reason)
        return cm.exception.decision
    def new_context(self,**changes):
        context=self.f.context.model_copy(update={'session_id':'session_b',**changes})
        token=self.f.registry.issue_session(context)
        self.f.service=Service(self.f.backend,self.f.root/'audit.jsonl',context.session_id,registry=self.f.registry,token=token)
    def test_authorized_read_allowed(self):
        self.assertEqual(self.f.call()['customer_id'],'customer_001')
        e=json.loads((self.f.root/'audit.jsonl').read_text())
        self.assertEqual(e['policy_decision'],'ALLOW');self.assertEqual(len(e['matched_grant_ids']),2)
    def test_authorized_close_changes_state(self):
        self.assertEqual(self.f.service.call('ticket.close',CLOSE)['status'],'closed')
    def test_refund_requires_approval_no_side_effect(self):
        self.block('refund.issue',REFUND,'REQUIRE_APPROVAL')
        with self.f.backend.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT refunded_minor FROM orders').fetchone()[0],0)
    def test_missing_user_capability_precedes_approval(self):
        self.store.revoke('user_a_refund_issue')
        self.block('refund.issue',REFUND,'DENY','USER_CAPABILITY_MISSING')
    def test_missing_agent_capability(self):
        self.store.revoke('agent_a_ticket_close')
        self.block('ticket.close',CLOSE,'DENY','AGENT_CAPABILITY_MISSING')
        self.assertEqual(self.f.backend.execute('ticket.read',{'ticket_id':'ticket_101'})['status'],'open')
    def test_task_action_denied(self):
        self.new_context(task_actions=('ticket.read',))
        self.block('ticket.close',CLOSE,'DENY','TASK_ACTION_MISMATCH')
    def test_task_resource_denied(self):
        self.new_context(task_scope=('customer:customer_001',))
        self.block('ticket.close',CLOSE,'DENY','TASK_RESOURCE_MISMATCH')
    def test_task_amount_denied(self):
        self.new_context(task_refund_limit_minor=1000)
        self.block('refund.issue',REFUND,'DENY','TASK_AMOUNT_OVER_LIMIT')
    def test_user_amount_constraint(self):
        grant=self.store.get('user_a_refund_issue')
        with self.store.registry.connect() as db:
            payload=json.loads(grant.model_dump_json());payload['conditions']['max_amount_minor']=1000
            db.execute('UPDATE capability_grants SET payload=? WHERE grant_id=?',(json.dumps(payload),grant.grant_id))
        self.block('refund.issue',REFUND,'DENY','USER_CAPABILITY_MISSING')
    def test_resource_tenant_from_database(self):
        with self.f.backend.connect() as db: db.execute("UPDATE resource_owners SET tenant_id='foreign' WHERE resource_type='ticket'")
        self.block('ticket.close',CLOSE,'DENY','RESOURCE_TENANT_MISMATCH')
    def test_agent_ceiling(self):
        with self.f.registry.connect() as db:
            agent=self.f.registry.get_agent('agent_a').model_copy(update={'allowed_capabilities':('ticket.read',)})
            db.execute("UPDATE principals SET payload=? WHERE id='agent_a'",(agent.model_dump_json(),))
        self.block('ticket.close',CLOSE,'DENY','AGENT_ACTION_CEILING')
    def test_expired_grant_checked_each_call(self):
        self.f.call()
        g=self.store.get('user_a_customer_read')
        payload=json.loads(g.model_dump_json());payload['expires_at']=self.f.now-0.5
        with self.store.registry.connect() as db: db.execute('UPDATE capability_grants SET payload=? WHERE grant_id=?',(json.dumps(payload),g.grant_id))
        self.block('crm.get_customer',{'customer_id':'customer_001'},'DENY','USER_CAPABILITY_MISSING')
    def test_approval_parameter_cannot_bypass(self):
        with self.assertRaises(ValueError): self.f.service.call('refund.issue',REFUND|{'approved':True})
        with self.f.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)
    def test_business_failure_is_denied_before_approval(self):
        with self.f.backend.connect() as db: db.execute('UPDATE orders SET refunded_minor=99999')
        with self.assertRaisesRegex(ValueError,'REFUND_EXCEEDS_BALANCE'): self.f.service.call('refund.issue',REFUND)
        e=json.loads((self.f.root/'audit.jsonl').read_text());self.assertEqual(e['policy_decision'],'DENY')
    def test_query_scope_binds_customer_filter(self):
        with self.f.backend.connect() as db:
            db.execute("INSERT INTO customers VALUES ('customer_002','Other','active')")
            db.execute("INSERT INTO resource_owners VALUES ('dataset','orders_customer_002','demo-tenant')")
        self.block('database.query',dict(query_id='orders_by_customer',customer_id='customer_002',limit=10),'DENY','AGENT_RESOURCE_CEILING')
    def test_unexpected_policy_error_fails_closed(self):
        from unittest.mock import patch
        with patch.object(self.f.service.policy,'evaluate',side_effect=RuntimeError('policy unavailable')):
            with self.assertRaises(RuntimeError): self.f.service.call('ticket.close',CLOSE)
        self.assertEqual(self.f.backend.execute('ticket.read',{'ticket_id':'ticket_101'})['status'],'open')

class PolicyMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_three_decisions_over_real_mcp(self):
        f=test_phase2.IdentityTests();f.setUp()
        try:
            async with connect(f.root/'business.sqlite',f.root/'mcp.jsonl','session_a',registry=f.registry,token=f.token) as session:
                good=await session.call_tool('ticket.read',{'ticket_id':'ticket_101'});self.assertFalse(good.is_error)
                pending=await session.call_tool('refund.issue',REFUND);self.assertTrue(pending.is_error)
                self.assertEqual(json.loads(pending.content[0].text)['decision'],'REQUIRE_APPROVAL')
                CapabilityStore(f.registry.path).revoke('user_a_ticket_close')
                denied=await session.call_tool('ticket.close',CLOSE);self.assertTrue(denied.is_error)
                self.assertEqual(json.loads(denied.content[0].text)['decision'],'DENY')
            with f.backend.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT status FROM tickets').fetchone()[0],'open')
            rows=[json.loads(x) for x in (f.root/'mcp.jsonl').read_text().splitlines()]
            self.assertEqual([r['policy_decision'] for r in rows],['ALLOW','REQUIRE_APPROVAL','DENY'])
        finally: f.tearDown()
