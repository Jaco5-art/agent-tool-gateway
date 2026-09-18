import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from agent_gateway.identity import AgentIdentity, ExecutionContext, IdentityRegistry, IdentityError
from agent_gateway.backend import Backend
from agent_gateway.service import Service
from agent_gateway.client import connect
from agent_gateway.policy.fixtures import ACTIONS, RESOURCES, provision_grants

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.registry=IdentityRegistry(self.root/'identity.sqlite')
        self.now=datetime.now(timezone.utc).timestamp()
        self.registry.register_user('user_a','demo-tenant',self.now+500)
        self.registry.register_agent(AgentIdentity(agent_id='agent_a',agent_name='Support',owner_id='owner_b',tenant_id='demo-tenant',purpose='Demo',allowed_capabilities=ACTIONS,allowed_resource_scope=RESOURCES,created_at=self.now-10,expires_at=self.now+500))
        self.context=ExecutionContext(user_id='user_a',agent_id='agent_a',tenant_id='demo-tenant',task_id='task_a',task_scope=RESOURCES,task_actions=ACTIONS,task_refund_limit_minor=50000,task_refund_currency='GBP',session_id='session_a',authenticated_at=self.now-1,expires_at=self.now+100)
        provision_grants(self.registry,self.context)
        self.token=self.registry.issue_session(self.context)
        self.backend=Backend(self.root/'business.sqlite')
        self.service=Service(self.backend,self.root/'audit.jsonl','session_a',registry=self.registry,token=self.token)
    def tearDown(self): self.temp.cleanup()
    def call(self): return self.service.call('crm.get_customer',{'customer_id':'customer_001'})
    def test_independent_identities_and_owner(self):
        c=self.registry.resolve(self.token)
        self.assertNotEqual(c.user_id,c.agent_id)
        self.assertNotEqual(self.registry.get_agent(c.agent_id).owner_id,c.user_id)
    def test_unknown_agent(self):
        with self.assertRaisesRegex(IdentityError,'UNKNOWN_AGENT'): self.registry.issue_session(self.context.model_copy(update={'agent_id':'missing'}))
    def test_unknown_user(self):
        with self.assertRaisesRegex(IdentityError,'UNKNOWN_USER'): self.registry.issue_session(self.context.model_copy(update={'user_id':'missing'}))
    def test_cross_tenant_rejected(self):
        with self.assertRaisesRegex(IdentityError,'TENANT_MISMATCH'): self.registry.issue_session(self.context.model_copy(update={'tenant_id':'other'}))
    def test_session_expiry_boundary(self):
        with self.assertRaisesRegex(IdentityError,'SESSION_EXPIRED'): self.registry.resolve(self.token,now=self.context.expires_at)
    def test_agent_expired(self):
        with self.registry.connect() as db: db.execute("UPDATE principals SET expires=? WHERE id='agent_a'",(self.now-1,))
        with self.assertRaisesRegex(IdentityError,'AGENT_EXPIRED'): self.call()
    def test_disabled_agent_on_next_call(self):
        self.call(); self.registry.set_status('agent_a','disabled')
        with self.assertRaisesRegex(IdentityError,'AGENT_DISABLED'): self.call()
    def test_disabled_user(self):
        self.registry.set_status('user_a','disabled')
        with self.assertRaisesRegex(IdentityError,'USER_DISABLED'): self.call()
    def test_revocation_prevents_mutation(self):
        self.registry.revoke('session_a')
        with self.assertRaisesRegex(IdentityError,'SESSION_REVOKED'):
            self.service.call('refund.issue',dict(order_id='order_438',amount_minor=100,currency='GBP',reason='test'))
        with self.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)
        event=json.loads((self.root/'audit.jsonl').read_text())
        self.assertEqual(event['agent_id'],'agent_a'); self.assertEqual(event['identity_status'],'rejected')
    def test_token_unknown(self):
        with self.assertRaisesRegex(IdentityError,'UNKNOWN_SESSION'): self.registry.resolve('forged')
    def test_duplicate_principal_cannot_overwrite(self):
        with self.assertRaises(sqlite3.IntegrityError): self.registry.register_user('agent_a','demo-tenant',self.now+500)
    def test_identity_spoof_rejected_and_audit_trusted(self):
        with self.assertRaises(ValueError): self.service.call('crm.get_customer',{'customer_id':'customer_001','user_id':'admin'})
        event=json.loads((self.root/'audit.jsonl').read_text())
        for k in ['user_id','agent_id','tenant_id','task_id','session_id','task_scope']: self.assertEqual(event[k],self.context.model_dump(mode='json')[k])
        self.assertNotIn(self.token,(self.root/'audit.jsonl').read_text())
    def test_sqlite_connection_closed(self):
        with self.backend.connect() as db: db.execute('SELECT 1')
        with self.assertRaises(sqlite3.ProgrammingError): db.execute('SELECT 1')
    def test_session_mismatch(self):
        service=Service(self.backend,self.root/'audit.jsonl','other',registry=self.registry,token=self.token)
        with self.assertRaisesRegex(IdentityError,'SESSION_MISMATCH'): service.call('ticket.read',{'ticket_id':'ticket_101'})

class IdentityMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_survives_mcp_and_revocation(self):
        fixture=IdentityTests(); fixture.setUp()
        try:
            async with connect(fixture.root/'business.sqlite',fixture.root/'mcp-audit.jsonl','session_a',registry=fixture.registry,token=fixture.token) as session:
                good=await session.call_tool('ticket.read',{'ticket_id':'ticket_101'})
                self.assertFalse(good.is_error)
                fixture.registry.revoke('session_a')
                bad=await session.call_tool('ticket.close',{'ticket_id':'ticket_101','resolution':'Done','expected_version':1})
                self.assertTrue(bad.is_error)
            with fixture.backend.connect() as db: self.assertEqual(db.execute('SELECT status FROM tickets').fetchone()[0],'open')
            events=[json.loads(x) for x in (fixture.root/'mcp-audit.jsonl').read_text().splitlines()]
            self.assertEqual([e['identity_status'] for e in events],['validated','rejected'])
            self.assertTrue(all(e['agent_id']=='agent_a' for e in events))
        finally: fixture.tearDown()
