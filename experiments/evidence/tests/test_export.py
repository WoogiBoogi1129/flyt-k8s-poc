import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'export_development.py'
class ExportTests(unittest.TestCase):
    def test_credentials_pod_env_and_symlink_are_not_exported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';case=source/'run-vm';case.mkdir(parents=True)
            (case/'metrics.json').write_text(json.dumps({'status':'PASS','scope':'development'}))
            (case/'timeline.json').write_text('[]')
            (source/'guest-key').write_text('private-test-fixture')
            (case/'Secret.json').write_text('{"data":"private-test-fixture"}')
            (case/'pod.json').write_text('{"env":"private-test-fixture"}')
            (case/'manifest.json').write_text(json.dumps({'phase':'development','seed':2026,
                'credentials':'private-test-fixture','channel_spec':{'future_secret':'private-test-fixture'}}))
            (case/'guest-stderr.txt').symlink_to(source/'guest-key')
            (case/'guest-stdout.txt').write_text('{"status":"PASS"}\n')
            linked=source/'linked-run';linked.mkdir()
            (linked/'timeline.json').write_text('[]')
            (linked/'metrics.json').symlink_to(case/'Secret.json')
            output=root/'public'
            subprocess.run([sys.executable,str(SCRIPT),'--source',str(source),'--output',str(output)],check=True,capture_output=True)
            files=[p for p in output.rglob('*') if p.is_file()]
            self.assertTrue((output/'run-vm/metrics.json').exists())
            self.assertTrue((output/'run-vm/guest-stdout.txt').exists())
            self.assertFalse((output/'run-vm/guest-stderr.txt').exists())
            self.assertEqual(json.loads((output/'run-vm/manifest.json').read_text())['case_id'],'run-vm')
            self.assertTrue(all(b'private-test-fixture' not in p.read_bytes() for p in files))

if __name__=='__main__':unittest.main()
