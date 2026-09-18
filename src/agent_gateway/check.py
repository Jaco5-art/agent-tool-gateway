"""Five real MCP calls, or five paid live-model tasks. Never substitute one for the other."""
import argparse
import asyncio
import json
import os
import platform
import tempfile
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from dotenv import load_dotenv
from .client import connect
from .backend import Backend

CASES=[
 ('customer','crm.get_customer',{'customer_id':'customer_001'},'Read customer customer_001.'),
 ('ticket','ticket.read',{'ticket_id':'ticket_101'},'Read ticket ticket_101.'),
 ('close','ticket.close',{'ticket_id':'ticket_101','resolution':'Resolved','expected_version':1},'Read ticket_101, then close it with resolution Resolved.'),
 ('refund','refund.issue',{'order_id':'order_438','amount_minor':1200,'currency':'GBP','reason':'Demo'},'Issue a simulated refund of GBP 12.00 for order_438 with reason Demo.'),
 ('query','database.query',{'query_id':'orders_by_customer','customer_id':'customer_001','limit':10},'Query orders_by_customer for customer_001, limit 10.'),
]

def verify(case, db):
    with Backend(db).connect() as con:
        if case=='close':
            row=con.execute('SELECT status,resolution,version FROM tickets').fetchone()
            return tuple(row)==('closed','Resolved',2)
        if case=='refund':
            rows=con.execute('SELECT amount_minor,currency FROM refunds').fetchall()
            return len(rows)==0 and con.execute('SELECT refunded_minor FROM orders').fetchone()[0]==0
    return True

async def check(live=False):
    report={'mode':'live_model' if live else 'mcp_protocol','timestamp':datetime.now(timezone.utc).isoformat(),
            'python':platform.python_version(),'versions':{p:version(p) for p in ['mcp','openai-agents','pydantic']},
            'authorization':'phase4_policy_enforced','cases':[]}
    if live and not os.getenv('OPENAI_API_KEY'):
        report.update(status='not_run',reason='OPENAI_API_KEY_missing')
        return report
    for name,tool,args,prompt in CASES:
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            db=root/'business.sqlite'; audit=root/'audit.jsonl'
            item={'case_id':name,'expected_tool':tool,'expected_decision':'REQUIRE_APPROVAL' if name=='refund' else 'ALLOW','passed':False}
            try:
                if live:
                    from .runtime import run_agent
                    item['agent']=await run_agent(prompt,root,os.getenv('OPENAI_MODEL','gpt-5-mini'))
                else:
                    async with connect(db,audit,name) as session:
                        manifest=await session.list_tools()
                        assert len(manifest.tools)==5
                        item['protocol_version']=session.protocol_version
                        result=await session.call_tool(tool,args)
                        if name=='refund':
                            assert result.is_error
                            data=json.loads(result.content[0].text)
                            assert data['decision']=='REQUIRE_APPROVAL'
                        else:
                            assert not result.is_error, result
                            data=result.structured_content
                        if name=='customer': assert data['customer_id']=='customer_001'
                        if name=='ticket': assert data['status']=='open'
                        if name=='query': assert data['row_count']==1 and data['rows'][0]['order_id']=='order_438'
                        item['output']=data
                events=[json.loads(line) for line in audit.read_text().splitlines()]
                # Verify successful call with the exact expected inputs using recorded hash.
                import hashlib
                expected_hash=hashlib.sha256(json.dumps(args,sort_keys=True).encode()).hexdigest()
                assert any(e['tool']==tool and e['execution_status']==('blocked' if name=='refund' else 'succeeded') and e['policy_decision']==item['expected_decision'] and e['parameters_hash']==expected_hash for e in events)
                assert verify(name,db)
                assert all(e.get('identity_status')=='validated' and e['user_id']!=e['agent_id'] and e.get('task_id') for e in events)
                item.update(passed=True,audit=events)
            except Exception as exc:
                item['error_type']=type(exc).__name__
                item['error']=str(exc)[:1000]
            report['cases'].append(item)
    report['passed']=sum(c['passed'] for c in report['cases'])
    report['status']='passed' if report['passed']==len(CASES) else 'failed'
    return report

def main():
    load_dotenv()
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true',help='Calls OpenAI API; incurs usage cost.')
    parser.add_argument('--output',default='results/local-check.json')
    args=parser.parse_args()
    report=asyncio.run(check(args.live))
    path=Path(args.output); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'mode':report['mode'],'passed':report.get('passed'), 'output':str(path)}))
    raise SystemExit(0 if report['status']=='passed' else 2)

if __name__ == '__main__':
    main()
