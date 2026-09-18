"""Small deterministic fixture benchmark, not a live-model task-success evaluation."""
import tempfile
from pathlib import Path
from uuid import uuid4
from agent_gateway.identity import local_session
from agent_gateway.client import connect
from agent_gateway.check import CASES

PROFILES={
 'customer_read':('customer.read',),
 'ticket_operations':('ticket.read','ticket.close'),
 'support':('customer.read','ticket.read','ticket.close'),
 'full_demo':('customer.read','ticket.read','ticket.close','refund.issue','database.query'),
}
TOOL_ACTION={'crm.get_customer':'customer.read','ticket.read':'ticket.read','ticket.close':'ticket.close','refund.issue':'refund.issue','database.query':'database.query'}

async def benchmark():
    rows=[]
    for profile,actions in PROFILES.items():
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            registry,token=local_session(root/'identity.sqlite','seed')
            context=registry.resolve(token).model_copy(update={'session_id':'session_'+uuid4().hex,'task_actions':actions})
            token=registry.issue_session(context)
            async with connect(root/'business.sqlite',root/'audit.jsonl',context.session_id,registry=registry,token=token) as session:
                names=[t.name for t in (await session.list_tools()).tools]
                expected=[tool for tool,action in TOOL_ACTION.items() if action in actions]
                if set(names)!=set(expected): raise AssertionError('Manifest differs from independently specified profile')
                passed=0;pending=0
                for case,tool,args,_ in CASES:
                    if tool not in expected: continue
                    result=await session.call_tool(tool,args)
                    if tool=='refund.issue':
                        import json
                        if not result.is_error or json.loads(result.content[0].text)['decision']!='REQUIRE_APPROVAL': raise AssertionError('Refund was not held')
                        pending+=1
                    else:
                        if result.is_error: raise AssertionError('Authorized call failed')
                        passed+=1
                rows.append({'profile':profile,'static_catalog_count':5,'visible_tools':names,'visible_count':len(names),'exposure_reduction':1-len(names)/5,'authorized_executable_calls_passed':passed,'approval_required_calls':pending})
    return {'scope':'four_handcrafted_profiles_no_llm','profiles':rows,'average_static_tool_count':5,
            'average_visible_tool_count':sum(r['visible_count'] for r in rows)/len(rows),
            'tool_exposure_reduction':1-sum(r['visible_count'] for r in rows)/(5*len(rows)),
            'authorized_executable_calls_passed':sum(r['authorized_executable_calls_passed'] for r in rows),
            'approval_required_calls':sum(r['approval_required_calls'] for r in rows),
            'live_model_task_success':'not_measured','static_baseline_task_success':'not_measured'}
