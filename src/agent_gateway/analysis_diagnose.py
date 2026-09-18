"""One reference E2B call via actual MCP; no model or repeated regression run."""
import argparse
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
from dotenv import load_dotenv
from .analysis.fixtures import REFERENCE_CODE,EXPECTED,seed_analysis
from .analysis.e2b_runner import safe_diagnostic
from .backend import Backend
from .client import connect

async def diagnose():
    report={'timestamp':datetime.now(timezone.utc).isoformat(),'mode':'single_reference_e2b_mcp','model_called':False}
    try:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seed_analysis(Backend(root/'business.sqlite'))
            async with connect(root/'business.sqlite',root/'audit.jsonl',e2b_analysis=True) as session:
                reply=await session.call_tool('analysis.run',{'customer_id':'customer_001','limit':100,'code':REFERENCE_CODE})
                body=json.loads(reply.content[0].text)
                report['response']=body
                report['status']='passed' if not reply.is_error and body.get('result')==EXPECTED else 'failed'
    except Exception as exc:
        report.update(status='failed',diagnostic=safe_diagnostic(exc,'mcp_transport'))
    return report

def main():
    load_dotenv()
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='results/phase9-e2b-diagnostic.json')
    args=parser.parse_args();report=asyncio.run(diagnose())
    p=Path(args.output);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['status']=='passed' else 1)

if __name__=='__main__': main()
