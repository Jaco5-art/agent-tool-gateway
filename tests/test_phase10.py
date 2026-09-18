import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from agent_gateway.containment.cases import CASES,REPLAYS
from agent_gateway.containment.fixture import setup,snapshot,authorized,private_disclosed,CANARY
from agent_gateway.containment.experiment import replay,trial

class ContainmentFixtureTests(unittest.TestCase):
    def test_grant_boundary_and_unchanged_reads(self):
        with tempfile.TemporaryDirectory() as d:
            backend,registry,token=setup(d)
            from agent_gateway.service import Service
            from agent_gateway.binding.manifest import visible_tools
            service=Service(backend,Path(d)/'audit.jsonl','containment',registry=registry,token=token)
            self.assertEqual(visible_tools(service),['crm.get_customer','ticket.read'])
            before=snapshot(backend)
            service.call('ticket.read',{'ticket_id':'ticket_101'})
            self.assertEqual(before,snapshot(backend))
    def test_summary_is_the_injection_source(self):
        with tempfile.TemporaryDirectory() as d:
            backend,_,_=setup(d,CASES[1]['payload'])
            row=backend.execute('ticket.read',{'ticket_id':'ticket_101'})
            self.assertIn(CASES[1]['payload'],row['summary'])
    def test_mutation_changes_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            backend,_,_=setup(d);before=snapshot(backend)
            backend.execute('ticket.close',{'ticket_id':'ticket_101','resolution':'test','expected_version':1})
            self.assertNotEqual(before,snapshot(backend))
    def test_authority_oracle_rejects_spoofed_fields(self):
        self.assertTrue(authorized('ticket.read',{'ticket_id':'ticket_101'}))
        self.assertFalse(authorized('ticket.read',{'ticket_id':'ticket_101','user_id':'admin'}))
        self.assertFalse(authorized('crm.get_customer',{'customer_id':'customer_002'}))
    def test_private_marker_oracle(self):
        self.assertTrue(private_disclosed({'name':CANARY}))
        self.assertFalse(private_disclosed({'customer_id':'customer_002'}))

class ContainmentMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_forced_replay_actual_mcp(self):
        with tempfile.TemporaryDirectory() as d:
            report=await replay(Path(d)/'replay')
            self.assertEqual(report['status'],'passed')
            self.assertEqual(report['blocked_calls'],len(REPLAYS))
            self.assertTrue(report['business_state_unchanged'])
            self.assertTrue(all(e['execution_status']=='blocked' for e in report['audit_events'][1:]))

    async def run_simulated_model(self,attempt=False,leak=False,read=True):
        # Synthetic model behavior only; real MCP and gateway are exercised.
        async def fake_run(agent,*args,**kwargs):
            if read:
                tool=next(t for t in agent.tools if t.name=='ticket__read')
                await tool.on_invoke_tool(None,json.dumps({'ticket_id':'ticket_101'}))
            if attempt:
                tool=next(t for t in agent.tools if t.name=='ticket__close')
                await tool.on_invoke_tool(None,json.dumps({'ticket_id':'ticket_101','resolution':'Injected','expected_version':1}))
            return SimpleNamespace(final_output=CANARY if leak else 'Delayed parcel.',context_wrapper=SimpleNamespace(usage=SimpleNamespace(requests=0,input_tokens=0,output_tokens=0,total_tokens=0)))
        with tempfile.TemporaryDirectory() as d,patch('agent_gateway.containment.experiment.Runner.run',side_effect=fake_run):
            return await trial(Path(d),CASES[1],'static','synthetic-test-double')
    async def test_model_attempt_and_execution_are_separate(self):
        report=await self.run_simulated_model(attempt=True)
        self.assertEqual(report['status'],'passed')
        self.assertEqual(report['unauthorized_tool_calls'],1)
        self.assertEqual(report['blocked_unauthorized_calls'],1)
        self.assertFalse(report['observed_unauthorized_execution'])
    async def test_no_attempt_rate_is_null_not_perfect(self):
        report=await self.run_simulated_model()
        self.assertIsNone(report['gateway_block_rate'])
        self.assertEqual(report['unauthorized_tool_calls'],0)
    async def test_unread_source_not_a_success(self):
        report=await self.run_simulated_model(read=False)
        self.assertEqual(report['status'],'failed')
    async def test_disclosed_canary_fails(self):
        report=await self.run_simulated_model(leak=True)
        self.assertEqual(report['status'],'failed')
