"""Run local Phase 4 acceptance without any API calls."""
import argparse
import io
import json
import unittest
from pathlib import Path
from datetime import datetime, timezone

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='results/local-phase4-check.json')
    args=parser.parse_args()
    tests=Path(__file__).resolve().parents[2]/'tests'
    if not tests.exists(): parser.error('Run this from the editable source installation (pip install -e .).')
    stream=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(tests))
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report={'phase':4,'mode':'policy_enforcement_with_real_mcp_and_regression','timestamp':datetime.now(timezone.utc).isoformat(),'status':'passed' if result.wasSuccessful() else 'failed','tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'live_model':'not_run_not_required_for_policy_tests','output':stream.getvalue()}
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='output'},ensure_ascii=False))
    raise SystemExit(0 if result.wasSuccessful() else 1)

if __name__=='__main__': main()
