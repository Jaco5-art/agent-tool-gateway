"""Run local Phase 6 acceptance without any API calls."""
import argparse
import asyncio
from .binding.benchmark import benchmark
import io
import json
import unittest
from pathlib import Path
from datetime import datetime, timezone

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='results/local-phase6-check.json')
    args=parser.parse_args()
    tests=Path(__file__).resolve().parents[2]/'tests'
    if not tests.exists(): parser.error('Run this from the editable source installation (pip install -e .).')
    stream=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(tests))
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report={'phase':6,'mode':'persistent_approval_with_mcp_and_regression','timestamp':datetime.now(timezone.utc).isoformat(),'status':'passed' if result.wasSuccessful() else 'failed','tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'live_model':'not_run_automated_reviewer_fixture','output':stream.getvalue()}
    if result.wasSuccessful():
        try:
            report['binding_benchmark']=asyncio.run(benchmark())
        except Exception as exc:
            report['status']='failed'
            report['benchmark_error']=str(exc)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'output','binding_benchmark'}},ensure_ascii=False))
    raise SystemExit(0 if report['status']=='passed' else 1)

if __name__=='__main__': main()
