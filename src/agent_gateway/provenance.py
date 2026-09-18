"""Offline evidence export. No raw credentials, parameters or business results."""
import argparse
import html
import json
from pathlib import Path

FIELDS='audit_schema_version trace_id parent_request_id request_id session_id task_id user_id agent_id tenant_id identity_status tool action resource_type resource_id risk_level policy_decision policy_version reason_code matched_grant_ids delegation_chain delegation_leaf_id approval_id reviewer_id approved_at jit_token_id sandbox_id provider code_sha256 dataset_sha256 rows_supplied execution_environment execution_status result_sha256 parameters_hash error_type latency_ms timestamp'.split()

def project(event,source):
    return {k:event[k] for k in FIELDS if k in event}|{'evidence_source':source,'trace_status':'recorded' if event.get('trace_id') else 'legacy_unlinked'}

def import_report(path):
    """Only audit_events arrays, never model messages or diagnostic payloads."""
    result=[]
    def walk(node):
        if isinstance(node,dict):
            for key,value in node.items():
                if key=='audit_events' and isinstance(value,list):
                    result.extend(project(e,'historical:'+Path(path).name) for e in value if isinstance(e,dict))
                else: walk(value)
        elif isinstance(node,list):
            for value in node: walk(value)
    walk(json.loads(Path(path).read_text(encoding='utf-8')))
    return result

def export(audit_path,approval_store=None):
    events=[project(json.loads(line),'current_audit') for line in Path(audit_path).read_text(encoding='utf-8').splitlines() if line.strip()]
    timeline=[]
    if approval_store:
        aids={e['approval_id'] for e in events if e.get('approval_id')}
        with approval_store.registry.connect() as db:
            for aid in sorted(aids):
                rows=db.execute('SELECT event,actor,timestamp FROM approval_events WHERE approval_id=? ORDER BY seq',(aid,)).fetchall()
                timeline.extend(dict(row)|{'approval_id':aid} for row in rows)
    return {'schema_version':1,'scope':'local gateway action provenance','events':events,'approval_timeline':timeline,'limitations':['Not a model reasoning trace or tamper-proof log.','Grant IDs are decision-time references, not full historical grant snapshots.','Legacy events have no fabricated trace IDs.','Reviewer actions in the demo are scripted fixtures, not a real human review.']}

def render(report):
    # All imported text remains text: no embedded JSON scripts or innerHTML.
    rows=[]
    for event in report['events']:
        label=' | '.join(str(event.get(k,'unknown')) for k in ('policy_decision','tool','execution_status'))
        rows.append('<details><summary>'+html.escape(label)+'</summary><pre>'+html.escape(json.dumps(event,ensure_ascii=False,indent=2))+'</pre></details>')
    return '''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Agent Action Provenance</title><style>body{background:#101827;color:#e7edf6;font:16px system-ui;max-width:1050px;margin:40px auto;padding:24px}h1{color:#78ddd2}input{padding:12px;width:90%;margin:18px 0}details{background:#1c293c;padding:16px;margin:12px 0;border-radius:10px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;color:#cad6e8}p{line-height:1.6}</style><h1>Agent Action Provenance</h1><p>身份 → 权限判定 → 审批 → 执行证据。展开查看记录；搜索工具、身份或 trace_id。</p><input id="filter" placeholder="搜索 / Search"><main>'''+''.join(rows)+'''</main><h2>审批时间线</h2><pre>'''+html.escape(json.dumps(report['approval_timeline'],ensure_ascii=False,indent=2))+'''</pre><h2>证据范围</h2><pre>'''+html.escape('\n'.join(report['limitations']))+'''</pre><script>document.getElementById('filter').addEventListener('input',function(){const q=this.value.toLowerCase();document.querySelectorAll('details').forEach(x=>x.hidden=!x.textContent.toLowerCase().includes(q));});</script></html>'''

def save(report,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    path.with_suffix('.html').write_text(render(report),encoding='utf-8')

def main():
    p=argparse.ArgumentParser();p.add_argument('--audit',required=True);p.add_argument('--output',default='results/provenance.json');a=p.parse_args()
    save(export(a.audit),a.output)
if __name__=='__main__': main()
