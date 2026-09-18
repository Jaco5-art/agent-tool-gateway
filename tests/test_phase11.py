import json,tempfile,unittest
from pathlib import Path
from agent_gateway.provenance import project,render,import_report
from agent_gateway.provenance_check import scenario
class ProvenanceTests(unittest.TestCase):
    def test_real_approval_link(self):
        with tempfile.TemporaryDirectory() as root: report=scenario(root)
        e=report['events'];self.assertNotEqual(e[2]['request_id'],e[3]['request_id']);self.assertEqual(len(e[3]['result_sha256']),64);self.assertTrue(e[3]['matched_grant_ids'])
    def test_legacy(self):
        e=project({'request_id':'old','sandbox_id':'real'},'historical');self.assertEqual(e['trace_status'],'legacy_unlinked');self.assertNotIn('trace_id',e);self.assertEqual(e['sandbox_id'],'real')
    def test_secrets(self):
        e=project({'tool':'read','token':'secret','parameters':{'key':'secret'},'result':'secret'},'test');self.assertNotIn('secret',json.dumps(e))
    def test_html(self):
        attack='</pre><script>alert(1)</script>';output=render({'events':[{'tool':attack}],'approval_timeline':[],'limitations':[]});self.assertNotIn(attack,output);self.assertIn('&lt;script&gt;',output);self.assertNotIn('innerHTML',output)
    def test_import(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'r.json';p.write_text(json.dumps({'messages':[{'tool':'fake'}],'nested':{'audit_events':[{'tool':'ticket.read','token':'secret'}]}}));e=import_report(p)
        self.assertEqual(len(e),1);self.assertNotIn('token',e[0])
