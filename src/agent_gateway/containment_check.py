"""Phase 10 acceptance: deterministic containment replay and optional real model."""
import argparse
import asyncio
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from dotenv import load_dotenv
from .containment.experiment import replay,live
from .containment.cases import CASES

def main():
    load_dotenv();parser=argparse.ArgumentParser()
    parser.add_argument('--live-model',action='store_true')
    parser.add_argument('--case',choices=[c['id'] for c in CASES],help='Only one scenario in both tool-exposure conditions.')
    parser.add_argument('--output',default='results/local-phase10-check.json')
    args=parser.parse_args();tests=Path(__file__).resolve().parents[2]/'tests'
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.discover(str(tests)))
    report={'phase':10,'timestamp':datetime.now(timezone.utc).isoformat(),'tests_run':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'regression_status':'passed' if result.wasSuccessful() else 'failed','test_output':stream.getvalue(),
        'replay':{'status':'not_run'},'live_model':{'status':'not_run'}}
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        if result.wasSuccessful(): report['replay']=asyncio.run(replay(root/'replay'))
        if args.live_model and report['replay']['status']=='passed':
            (root/'live').mkdir()
            report['live_model']=asyncio.run(live(root/'live',[args.case] if args.case else None))
    report['status']='failed' if not result.wasSuccessful() or report['replay']['status']!='passed' else report['live_model']['status'] if args.live_model else 'local_passed_live_pending'
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('test_output','replay','live_model')},ensure_ascii=False))
    print(json.dumps({'replay_status':report['replay']['status'],'live_model_status':report['live_model']['status'],'output':str(output)}))
    raise SystemExit(1 if report['status']=='failed' else 2 if report['status']=='blocked' else 0)

if __name__=='__main__': main()
