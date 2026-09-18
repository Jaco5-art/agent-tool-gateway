import json
import unittest
import test_phase2
from agent_gateway.binding.manifest import visible_tools
from agent_gateway.policy.capabilities import CapabilityStore
from agent_gateway.service import Service
from agent_gateway.client import connect
from agent_gateway.runtime import bind_tools

class BindingTests(unittest.TestCase):
    def setUp(self): self.f=test_phase2.IdentityTests();self.f.setUp()
    def tearDown(self): self.f.tearDown()
    def new_context(self,**changes):
        context=self.f.context.model_copy(update={'session_id':'session_b',**changes})
        token=self.f.registry.issue_session(context)
        self.f.service=Service(self.f.backend,self.f.root/'audit.jsonl',context.session_id,registry=self.f.registry,token=token)
    def test_full_manifest(self): self.assertEqual(len(visible_tools(self.f.service)),5)
    def test_read_only_task(self):
        self.new_context(task_actions=('ticket.read',))
        self.assertEqual(visible_tools(self.f.service),['ticket.read'])
    def test_resource_scope(self):
        self.new_context(task_scope=('customer:customer_001',))
        self.assertEqual(visible_tools(self.f.service),['crm.get_customer'])
    def test_no_common_user_grant(self):
        CapabilityStore(self.f.registry.path).revoke('user_a_ticket_read')
        self.assertNotIn('ticket.read',visible_tools(self.f.service))
    def test_no_common_agent_grant(self):
        CapabilityStore(self.f.registry.path).revoke('agent_a_ticket_read')
        self.assertNotIn('ticket.read',visible_tools(self.f.service))
    def test_revocation_refresh(self):
        self.assertEqual(len(visible_tools(self.f.service)),5)
        self.f.registry.revoke('session_a')
        self.assertEqual(visible_tools(self.f.service),[])
    def test_disabled_agent_empty(self):
        self.f.registry.set_status('agent_a','disabled')
        self.assertEqual(visible_tools(self.f.service),[])
    def test_zero_refund_limit_hides(self):
        self.new_context(task_refund_limit_minor=0)
        self.assertNotIn('refund.issue',visible_tools(self.f.service))
    def test_disjoint_currency_hides(self):
        self.new_context(task_refund_currency='USD')
        self.assertNotIn('refund.issue',visible_tools(self.f.service))
    def test_approval_eligible_tool_remains_visible(self): self.assertIn('refund.issue',visible_tools(self.f.service))
    def test_listing_has_no_business_side_effects(self):
        visible_tools(self.f.service)
        with self.f.backend.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT status FROM tickets').fetchone()[0],'open')
        self.assertFalse((self.f.root/'audit.jsonl').exists())
    def test_agent_ceiling_filters(self):
        agent=self.f.registry.get_agent('agent_a').model_copy(update={'allowed_capabilities':('customer.read',)})
        with self.f.registry.connect() as db: db.execute("UPDATE principals SET payload=? WHERE id='agent_a'",(agent.model_dump_json(),))
        self.assertEqual(visible_tools(self.f.service),['crm.get_customer'])
    def test_foreign_resource_hidden(self):
        with self.f.backend.connect() as db: db.execute("UPDATE resource_owners SET tenant_id='foreign' WHERE resource_type='customer'")
        self.assertNotIn('crm.get_customer',visible_tools(self.f.service))
    def test_expired_grant_hidden(self):
        store=CapabilityStore(self.f.registry.path);g=store.get('user_a_ticket_read')
        p=json.loads(g.model_dump_json());p['expires_at']=self.f.now-0.5
        with store.registry.connect() as db: db.execute('UPDATE capability_grants SET payload=? WHERE grant_id=?',(json.dumps(p),g.grant_id))
        self.assertNotIn('ticket.read',visible_tools(self.f.service))

class BindingMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_tool_direct_call_blocked(self):
        f=test_phase2.IdentityTests();f.setUp()
        try:
            CapabilityStore(f.registry.path).revoke('user_a_ticket_close')
            async with connect(f.root/'business.sqlite',f.root/'mcp.jsonl','session_a',registry=f.registry,token=f.token) as session:
                self.assertNotIn('ticket.close',[t.name for t in (await session.list_tools()).tools])
                result=await session.call_tool('ticket.close',dict(ticket_id='ticket_101',resolution='Done',expected_version=1))
                self.assertTrue(result.is_error)
                self.assertEqual(json.loads(result.content[0].text)['decision'],'DENY')
            self.assertEqual(f.backend.execute('ticket.read',{'ticket_id':'ticket_101'})['status'],'open')
        finally: f.tearDown()
    async def test_sdk_enabled_rechecks_manifest(self):
        f=test_phase2.IdentityTests();f.setUp()
        try:
            async with connect(f.root/'business.sqlite',f.root/'mcp.jsonl','session_a',registry=f.registry,token=f.token) as session:
                tools=await bind_tools(session)
                tool=next(t for t in tools if t.name=='ticket__read')
                self.assertTrue(await tool.is_enabled(None,None))
                CapabilityStore(f.registry.path).revoke('user_a_ticket_read')
                self.assertFalse(await tool.is_enabled(None,None))
                # Cached tool objects cannot bypass execution authorization either.
                result=json.loads(await tool.on_invoke_tool(None,'{"ticket_id":"ticket_101"}'))
                self.assertTrue(result['is_error'])
        finally: f.tearDown()
