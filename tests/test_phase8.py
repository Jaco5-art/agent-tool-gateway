import json
import unittest
import test_phase2
from agent_gateway.identity import AgentIdentity
from agent_gateway.policy.fixtures import ACTIONS, RESOURCES
from agent_gateway.policy.capabilities import CapabilityStore
from agent_gateway.policy.delegation import DelegationLink, DelegationStore, DelegationError
from agent_gateway.policy.engine import PolicyBlocked
from agent_gateway.service import Service
from agent_gateway.binding.manifest import visible_tools
from agent_gateway.client import connect

REFUND=dict(order_id='order_438',amount_minor=1200,currency='GBP',reason='Demo')

class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.f=test_phase2.IdentityTests();self.f.setUp()
        self.store=DelegationStore(self.f.registry.path);self.caps=CapabilityStore(self.f.registry.path)
        # Trusted fixture provisioning. All actors initially hold the same rights.
        with self.f.registry.connect() as db:
            rows=db.execute('SELECT grant_id,payload FROM capability_grants').fetchall()
            for row in rows:
                data=json.loads(row['payload']);data['delegable']=True
                db.execute('UPDATE capability_grants SET payload=? WHERE grant_id=?',(json.dumps(data),row['grant_id']))
        for agent in ['agent_b','agent_c']:
            self.f.registry.register_agent(AgentIdentity(agent_id=agent,agent_name=agent,owner_id='owner',tenant_id='demo-tenant',purpose='Delegated support',allowed_capabilities=ACTIONS,allowed_resource_scope=RESOURCES,created_at=self.f.now-10,expires_at=self.f.now+500))
            for action in ACTIONS:
                g=self.caps.get('agent_a_'+action.replace('.','_'))
                self.caps.add(g.model_copy(update={'grant_id':agent+'_'+action.replace('.','_'),'subject_id':agent,'delegable':agent=='agent_b'}))
        self.context=self.f.context.model_copy(update={'agent_id':'agent_c','session_id':'delegated-session','delegation_leaf_id':'link_c'})
        self.root=DelegationLink(delegation_id='link_a',issuer_id='user_a',subject_id='agent_a',user_id='user_a',tenant_id='demo-tenant',task_id='task_a',session_id='delegated-session',actions=ACTIONS,resources=RESOURCES,refund_limit_minor=50000,refund_currency='GBP',not_before=self.f.now-1,expires_at=self.f.now+80,remaining_depth=2)
        self.store.add(self.root)
        self.middle=self.root.model_copy(update={'delegation_id':'link_b','parent_id':'link_a','issuer_id':'agent_a','subject_id':'agent_b','remaining_depth':1})
        self.store.add(self.middle)
        self.leaf=self.middle.model_copy(update={'delegation_id':'link_c','parent_id':'link_b','issuer_id':'agent_b','subject_id':'agent_c','remaining_depth':0})
        self.store.add(self.leaf)
        self.token=self.f.registry.issue_session(self.context)
        self.service=Service(self.f.backend,self.f.root/'delegated.jsonl','delegated-session',registry=self.f.registry,token=self.token)
    def tearDown(self): self.f.tearDown()
    def alter(self,ident,**changes):
        link,_=self.store.get(ident)
        payload=json.loads(link.model_dump_json());payload.update(changes)
        with self.f.registry.connect() as db: db.execute('UPDATE delegations SET payload=? WHERE id=?',(json.dumps(payload),ident))
    def deny(self,tool='ticket.read',params=None):
        with self.assertRaises(PolicyBlocked) as cm: self.service.call(tool,params or {'ticket_id':'ticket_101'})
        self.assertEqual(cm.exception.decision.decision,'DENY')
        return cm.exception.decision.reason_code
    def test_valid_three_agent_chain(self):
        self.assertEqual(self.service.call('ticket.read',{'ticket_id':'ticket_101'})['status'],'open')
        audit=json.loads((self.f.root/'delegated.jsonl').read_text())
        self.assertEqual(audit['delegation_chain'],['link_a','link_b','link_c'])
    def test_user_read_scope_cannot_close(self):
        for ident in ['link_a','link_b','link_c']: self.alter(ident,actions=['ticket.read'])
        self.assertEqual(self.deny('ticket.close',dict(ticket_id='ticket_101',resolution='Done',expected_version=1)),'DELEGATION_REQUEST_OUT_OF_SCOPE')
        self.assertEqual(visible_tools(self.service),['ticket.read'])
    def test_upper_agent_capability_revoked(self):
        self.caps.revoke('agent_a_ticket_read')
        self.assertEqual(self.deny(),'DELEGATION_CAPABILITY_MISSING')
    def test_middle_agent_ceiling(self):
        agent=self.f.registry.get_agent('agent_b').model_copy(update={'allowed_capabilities':('customer.read',)})
        with self.f.registry.connect() as db: db.execute("UPDATE principals SET payload=? WHERE id='agent_b'",(agent.model_dump_json(),))
        self.assertEqual(self.deny(),'DELEGATION_AGENT_CEILING')
    def test_non_delegable_upstream(self):
        g=self.caps.get('agent_a_ticket_read');p=json.loads(g.model_dump_json());p['delegable']=False
        with self.f.registry.connect() as db: db.execute('UPDATE capability_grants SET payload=? WHERE grant_id=?',(json.dumps(p),g.grant_id))
        self.assertEqual(self.deny(),'DELEGATION_NOT_DELEGABLE')
    def test_non_delegable_user(self):
        g=self.caps.get('user_a_ticket_read');p=json.loads(g.model_dump_json());p['delegable']=False
        with self.f.registry.connect() as db: db.execute('UPDATE capability_grants SET payload=? WHERE grant_id=?',(json.dumps(p),g.grant_id))
        self.assertEqual(self.deny(),'DELEGATION_NOT_DELEGABLE')
    def test_parent_revocation(self):
        self.store.revoke('link_a');self.assertEqual(self.deny(),'DELEGATION_REVOKED')
        self.assertEqual(visible_tools(self.service),[])
    def test_expired_parent(self):
        self.alter('link_a',expires_at=self.f.now-0.5)
        self.assertEqual(self.deny(),'DELEGATION_EXPIRED_OR_NOT_YET_VALID')
    def test_disabled_middle(self):
        self.f.registry.set_status('agent_b','disabled')
        self.assertEqual(self.deny(),'DELEGATION_PRINCIPAL_INACTIVE')
    def test_missing_parent(self):
        self.alter('link_c',parent_id='missing');self.assertEqual(self.deny(),'DELEGATION_PARENT_MISSING')
    def test_cycle(self):
        self.alter('link_a',parent_id='link_c');self.assertEqual(self.deny(),'DELEGATION_CYCLE')
    def test_wrong_issuer(self):
        self.alter('link_c',issuer_id='user_a');self.assertEqual(self.deny(),'DELEGATION_ISSUER_MISMATCH')
    def test_cross_tenant_chain(self):
        for ident in ['link_a','link_b','link_c']: self.alter(ident,tenant_id='foreign')
        self.assertEqual(self.deny(),'DELEGATION_TENANT_MISMATCH')
    def test_repeated_principal_cycle(self):
        self.alter('link_c',subject_id='agent_a')
        self.assertEqual(self.deny(),'DELEGATION_PRINCIPAL_CYCLE')
    def test_wrong_task(self):
        for ident in ['link_a','link_b','link_c']: self.alter(ident,task_id='other')
        self.assertEqual(self.deny(),'DELEGATION_CONTEXT_MISMATCH')
    def test_action_expansion_rejected_at_add(self):
        self.alter('link_a',actions=['ticket.read'])
        bad=self.middle.model_copy(update={'delegation_id':'bad'})
        with self.assertRaisesRegex(DelegationError,'SCOPE_EXPANSION'): self.store.add(bad)
    def test_resource_expansion_rejected(self):
        bad=self.middle.model_copy(update={'delegation_id':'bad','resources':RESOURCES+('order:other',)})
        with self.assertRaisesRegex(DelegationError,'SCOPE_EXPANSION'): self.store.add(bad)
    def test_amount_expansion_rejected(self):
        bad=self.middle.model_copy(update={'delegation_id':'bad','refund_limit_minor':50001})
        with self.assertRaisesRegex(DelegationError,'AMOUNT_EXPANSION'): self.store.add(bad)
    def test_time_expansion_rejected(self):
        bad=self.middle.model_copy(update={'delegation_id':'bad','expires_at':self.f.now+90})
        with self.assertRaisesRegex(DelegationError,'TIME_EXPANSION'): self.store.add(bad)
    def test_depth_exceeded(self):
        bad=self.middle.model_copy(update={'delegation_id':'bad','remaining_depth':2})
        with self.assertRaisesRegex(DelegationError,'DEPTH_EXCEEDED'): self.store.add(bad)
    def test_leaf_limit_controls_amount(self):
        self.alter('link_c',refund_limit_minor=1000)
        self.assertEqual(self.deny('refund.issue',REFUND),'DELEGATION_AMOUNT_OUT_OF_SCOPE')
    def approved_token(self):
        with self.assertRaises(PolicyBlocked) as cm: self.service.call('refund.issue',REFUND)
        aid=cm.exception.decision.approval_id
        reviewer=self.service.approvals.register_reviewer('reviewer','demo-tenant',['refund.issue'],['order:order_438'],self.f.now+500)
        self.service.approvals.decide(aid,reviewer,'approve')
        token,meta=self.service.issue_jit(aid)
        return aid,token,meta
    def test_jit_bound_to_chain(self):
        aid,token,meta=self.approved_token()
        self.assertEqual(meta['delegation_leaf_id'],'link_c');self.assertTrue(meta['delegation_digest'])
        self.service.resume(aid,jit_token=token)
        with self.f.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],1)
    def test_parent_revoked_after_jit(self):
        aid,token,_=self.approved_token();self.store.revoke('link_a')
        with self.assertRaises(PolicyBlocked): self.service.resume(aid,jit_token=token)
        self.assertEqual(self.service.jit.inspect(token)['status'],'active')
        with self.f.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)
    def test_chain_changed_after_jit(self):
        aid,token,_=self.approved_token();self.alter('link_c',refund_limit_minor=40000)
        with self.assertRaisesRegex(ValueError,'DELEGATION_CHANGED'): self.service.resume(aid,jit_token=token)
        with self.f.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)

class DelegationMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_chain_controls_manifest_and_direct_calls(self):
        f=DelegationTests();f.setUp()
        try:
            for ident in ['link_a','link_b','link_c']: f.alter(ident,actions=['ticket.read'])
            async with connect(f.f.root/'business.sqlite',f.f.root/'mcp.jsonl','delegated-session',registry=f.f.registry,token=f.token) as session:
                self.assertEqual([t.name for t in (await session.list_tools()).tools],['ticket.read'])
                result=await session.call_tool('ticket.close',dict(ticket_id='ticket_101',resolution='Done',expected_version=1))
                self.assertTrue(result.is_error)
                self.assertEqual(json.loads(result.content[0].text)['decision'],'DENY')
                good=await session.call_tool('ticket.read',{'ticket_id':'ticket_101'});self.assertFalse(good.is_error)
            self.assertEqual(f.f.backend.execute('ticket.read',{'ticket_id':'ticket_101'})['status'],'open')
        finally: f.tearDown()
