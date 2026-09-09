import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('archive_xml',ROOT/'tools/archive_xml.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.source=self.base/'input'; self.source.mkdir()
        self.patch=patch.object(module,'ROOT',self.base/'office'); self.patch.start()
        self.a=json.loads((ROOT/'templates/xml-approval.json').read_text())
        self.a.update(decision='APPROVED_INTERNAL',approved_by='TEST ONLY',approved_at='2026-09-09',
                      review_status='PASS',validation_status='NOT_APPLICABLE',
                      validation_na_reason='Test fixture, not a construction format',
                      format_and_schema='TEST XML',publication_scope='approved_for_public_github')
        for kind,text in [('xml','<?xml version="1.0"?><test>fixture</test>'),('review','Test review'),('validation','Test validation')]:
            p=self.source/self.a[kind+'_file']; p.write_text(text)
            self.a[kind+'_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
        self.save()

    def save(self):
        self.approval=self.source/'approval.json'
        self.approval.write_text(json.dumps(self.a))

    def tearDown(self): self.patch.stop(); self.tmp.cleanup()

    def run_archive(self): return module.archive('PROJECT-001','v001',self.approval)

    def test_preserves_approved_bytes_and_rejects_overwrite(self):
        dest=self.run_archive()
        self.assertEqual((dest/'product.xml').read_bytes(),(self.source/'result.xml').read_bytes())
        self.assertEqual((dest/'approval.json').read_bytes(),self.approval.read_bytes())
        manifest=json.loads((dest/'manifest.json').read_text())
        for entry in manifest['files']:
            self.assertEqual(hashlib.sha256((dest/entry['path']).read_bytes()).hexdigest(),entry['sha256'])
        with self.assertRaises(FileExistsError): self.run_archive()

    def test_changed_xml_rejected(self):
        (self.source/'result.xml').write_text('<changed/>')
        with self.assertRaises(ValueError): self.run_archive()

    def test_missing_approval_review_or_publication_rejected(self):
        for field,value in [('decision','NOT_APPROVED'),('review_status','NOT_CHECKED'),
                            ('publication_scope','NOT_CONFIRMED'),('open_major',['ISSUE']),
                            ('validation_status','NOT_CHECKED')]:
            old=self.a[field]; self.a[field]=value; self.save()
            with self.assertRaises(ValueError): self.run_archive()
            self.a[field]=old

    def test_source_traversal_rejected(self):
        self.a['xml_file']='../outside.xml'; self.save()
        with self.assertRaises(ValueError): self.run_archive()

    def test_utf16_dtd_rejected(self):
        data='<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE test [<!ENTITY x "expanded">]><test>&x;</test>'.encode('utf-16')
        (self.source/'result.xml').write_bytes(data)
        self.a['xml_sha256']=hashlib.sha256(data).hexdigest(); self.save()
        with self.assertRaises(ValueError): self.run_archive()

    def test_required_customer_evidence_archived(self):
        self.a['customer_approval_required']=True; self.save()
        with self.assertRaises(ValueError): self.run_archive()
        data=b'TEST EVIDENCE ONLY'; (self.source/'evidence.txt').write_bytes(data)
        self.a['attachments']=[{'file':'evidence.txt','purpose':'customer_approval','sha256':hashlib.sha256(data).hexdigest()}]
        self.save(); dest=self.run_archive()
        self.assertEqual((dest/'attachments/001_evidence.txt').read_bytes(),data)

if __name__=='__main__': unittest.main()
