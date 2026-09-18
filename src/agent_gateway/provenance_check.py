import argparse,io,json,tempfile,time,unittest
from pathlib import Path
from .backend import Backend
from .identity import local_session
from .service import Service
from .policy.engine import PolicyBlocked
from .provenance import export,import_report,save

def scenario(root):
    root=Path(root);registry,token=local_session(root/'identity.sqlite','phase11-demo')
    service=Service(Backend(root/'business.sqlite'),root/'audit.jsonl','phase11-demo',registry=registry,token=token)
    service.call('ticket.read',{'ticket_id':'ticket_101'})
    try: service.call('ticket.read',{'ticket_id':'ticket_101','user_id':'admin'})
    except ValueError: pass
    try: service.call('refund.issue',dict(order_id='order_438',amount_minor=1200,currency='GBP',reason='Provenance fixture'))
    except PolicyBlocked as exc: aid=exc.decision.approval_id
    reviewer=service.approvals.register_reviewer('fixture-reviewer','demo-tenant',['refund.issue'],['order:order_438'],time.time()+600)
    service.approvals.decide(aid,reviewer,'approve');service.resume(aid)
    report=export(root/'audit.jsonl',service.approvals);e=report['events']
    assert len(e)==4 and e[0]['execution_status']=='succeeded' and e[1]['execution_status']=='blocked'
    assert e[2]['policy_decision']=='REQUIRE_APPROVAL'
    assert e[3]['trace_id']==e[2]['trace_id'] and e[3]['parent_request_id']==e[2]['request_id']
    assert e[3]['execution_status']=='succeeded' and e[3]['reviewer_id']=='fixture-reviewer'
    assert [x['event'] for x in report['approval_timeline']]==['requested','approved','jit_issued','jit_consumed','executing','executed']
    assert token not in json.dumps(report) and reviewer not in json.dumps(report)
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default='results/local-phase11-check.json');a=p.parse_args()
    base=Path(__file__).resolve().parents[2];stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.discover(str(base/'tests')))
    report={'phase':11,'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'test_output':stream.getvalue(),'model_called':False,'e2b_called':False}
    try:
        with tempfile.TemporaryDirectory() as directory: evidence=scenario(directory)
        report['local_scenario']='passed'
        for name in ['phase9-local-accepted.json','phase10-local-accepted.json']:
            source=base/'results'/name
            if source.exists(): evidence['events'].extend(import_report(source))
        save(evidence,Path(a.output).with_name('phase11-provenance.json'))
        report['evidence_events']=len(evidence['events']);report['status']='passed' if result.wasSuccessful() else 'failed'
    except Exception as exc: report.update(status='failed',error=type(exc).__name__+': '+str(exc))
    output=Path(a.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='test_output'}));raise SystemExit(0 if report['status']=='passed' else 1)
if __name__=='__main__': main()
