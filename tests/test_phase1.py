import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from pydantic import ValidationError
from agent_gateway.backend import Backend, BusinessError
from agent_gateway.identity import local_session
from agent_gateway.service import Service
from agent_gateway.contracts import TOOLS
from agent_gateway.client import connect
from agent_gateway.runtime import bind_tools

class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.backend=Backend(self.root/'test.sqlite')
        registry,token=local_session(self.root/'identity.sqlite','test')
        self.service=Service(self.backend,self.root/'audit.jsonl','test',registry=registry,token=token)
    def tearDown(self):
        self.temp.cleanup()
    def refund(self, **kwargs):
        from agent_gateway.contracts import RefundInput
        p=RefundInput.model_validate(dict(order_id='order_438',amount_minor=1200,currency='GBP',reason='Demo') | kwargs)
        return self.backend.execute('refund.issue',p.model_dump())
    def test_missing_customer(self):
        with self.assertRaises(BusinessError): self.service.call('crm.get_customer',{'customer_id':'missing'})
    def test_strict_money_types(self):
        for value in [-1,0,1.5,'1200',True]:
            with self.subTest(value=value), self.assertRaises(ValidationError): self.refund(amount_minor=value)
        with self.backend.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)
    def test_currency_failure_has_no_effect(self):
        with self.assertRaises(BusinessError): self.refund(currency='USD')
        with self.backend.connect() as db: self.assertEqual(db.execute('SELECT refunded_minor FROM orders').fetchone()[0],0)
    def test_over_refund_rolls_back(self):
        self.refund(amount_minor=90000)
        with self.assertRaises(BusinessError): self.refund(amount_minor=20000)
        with self.backend.connect() as db:
            self.assertEqual(db.execute('SELECT refunded_minor FROM orders').fetchone()[0],90000)
            self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],1)
    def test_concurrent_refunds_cannot_exceed_balance(self):
        def attempt(_):
            try: self.refund(amount_minor=60000); return True
            except BusinessError: return False
        with ThreadPoolExecutor(max_workers=2) as pool: self.assertEqual(sum(pool.map(attempt,range(2))),1)
    def test_stale_ticket_no_mutation(self):
        with self.assertRaises(BusinessError):
            self.service.call('ticket.close',dict(ticket_id='ticket_101',resolution='Done',expected_version=9))
        self.assertEqual(self.service.call('ticket.read',{'ticket_id':'ticket_101'})['status'],'open')
    def test_ticket_closed_only_once(self):
        p=dict(ticket_id='ticket_101',resolution='Done',expected_version=1)
        self.assertEqual(self.service.call('ticket.close',p)['version'],2)
        with self.assertRaises(BusinessError): self.service.call('ticket.close',p)
    def test_extra_fields_rejected(self):
        with self.assertRaises(ValidationError): self.refund(user_id='admin')
    def test_arbitrary_sql_rejected(self):
        with self.assertRaises(ValidationError): self.service.call('database.query',{'query_id':'DROP TABLE orders','customer_id':'customer_001','limit':10})
    def test_query_limits(self):
        for n in [0,101]:
            with self.assertRaises(ValidationError): self.service.call('database.query',{'query_id':'orders_by_customer','customer_id':'customer_001','limit':n})
    def test_empty_query(self):
        result=self.backend.execute('database.query',{'query_id':'orders_by_customer','customer_id':'missing','limit':10})
        self.assertEqual(result['row_count'],0)
    def test_unknown_tool_audited(self):
        with self.assertRaises(ValueError): self.service.call('database.export',{})
        event=json.loads((self.root/'audit.jsonl').read_text())
        self.assertEqual(event['execution_status'],'blocked')
        self.assertEqual(event['policy_decision'],'DENY')
        self.assertNotIn('parameters',event)

class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    async def asyncTearDown(self): self.temp.cleanup()
    async def test_manifest_and_sdk_adapter(self):
        async with connect(self.root/'test.sqlite',self.root/'audit.jsonl') as session:
            manifest=await session.list_tools()
            self.assertEqual({t.name for t in manifest.tools},set(TOOLS))
            for t in manifest.tools:
                self.assertIsNotNone(t.output_schema)
                self.assertFalse(t.input_schema['additionalProperties'])
            tools=await bind_tools(session)
            tool=next(t for t in tools if t.name=='crm__get_customer')
            result=json.loads(await tool.on_invoke_tool(None,'{"customer_id":"customer_001"}'))
            self.assertFalse(result['is_error'])
            self.assertEqual(result['data']['customer_id'],'customer_001')
    async def test_protocol_invalid_call_no_side_effect(self):
        async with connect(self.root/'test.sqlite',self.root/'audit.jsonl') as session:
            result=await session.call_tool('refund.issue',{'order_id':'order_438','amount_minor':True,'currency':'GBP','reason':'Demo'})
            self.assertTrue(result.is_error)
            unknown=await session.call_tool('database.export',{})
            self.assertTrue(unknown.is_error)
        with Backend(self.root/'test.sqlite').connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM refunds').fetchone()[0],0)

if __name__=='__main__': unittest.main()
