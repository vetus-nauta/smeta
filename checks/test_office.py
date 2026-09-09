import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('office', ROOT/'tools/office.py')
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)

class OfficeChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT/'templates', self.root/'templates')
        self.root_patch = patch.object(office, 'ROOT', self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop(); self.tmp.cleanup()

    def call(self, *args):
        out = io.StringIO()
        with patch.object(sys, 'argv', ['office.py', *args]), contextlib.redirect_stdout(out):
            office.main()
        return out.getvalue()

    def test_project_creation_preserves_existing(self):
        self.call('new', 'T-001', '--name', 'Test')
        p=self.root/'projects/T-001'
        before=(p/'project.json').read_bytes()
        with self.assertRaises(FileExistsError): self.call('new','T-001','--name','Overwrite')
        self.assertEqual(before,(p/'project.json').read_bytes())
        self.assertTrue((p/'work/kac-offers.csv').is_file())

    def test_path_traversal_rejected(self):
        for code in ('../x','/tmp/x','a/b','..','x\\y'):
            with self.assertRaises(ValueError): office.project_path(code)

    def test_missing_metadata_cannot_pass_check(self):
        self.call('new','T-001','--name','Test')
        with self.assertRaises(SystemExit) as cm: self.call('check','T-001')
        self.assertEqual(cm.exception.code,2)

    def test_manifest_hashes_without_approving(self):
        self.call('new','T-001','--name','Test')
        p=self.root/'projects/T-001'
        with self.assertRaises(ValueError): self.call('manifest','T-001')
        (p/'release/educational.txt').write_text('Educational test only')
        self.call('manifest','T-001')
        m=json.loads((p/'manifest.json').read_text())
        self.assertEqual(len(m['files']),1)
        self.assertEqual(len(m['files'][0]['sha256']),64)
        self.assertEqual(json.loads((p/'project.json').read_text())['status'],'DRAFT')
        self.assertEqual(m['approval'],'NOT_EVALUATED')

if __name__ == '__main__': unittest.main()
