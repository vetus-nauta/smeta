#!/usr/bin/env python3
"""Archive an explicitly approved XML package; never approves or uploads it."""
import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class NoDTD(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError('DTD/entity declarations are not accepted')

def nonempty(value):
    return isinstance(value, str) and bool(value.strip())

def archive(project, version, approval):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', project):
        raise ValueError('Invalid project ID')
    if not re.fullmatch(r'v[0-9]{3,6}', version):
        raise ValueError('Version must be v001 or similar')
    approval = Path(approval).resolve()
    approval_bytes = approval.read_bytes()
    a = json.loads(approval_bytes)
    if a.get('project_id') != project or a.get('version') != version:
        raise ValueError('Approval does not match project/version')
    if a.get('decision') != 'APPROVED_INTERNAL' or a.get('review_status') != 'PASS':
        raise ValueError('Explicit approval and completed review required')
    if a.get('approval_scope') != 'internal_department':
        raise ValueError('This tool handles internal department approval only')
    for field in ('approved_by','approved_at','format_and_schema'):
        if not nonempty(a.get(field)): raise ValueError('Missing '+field)
    dt.date.fromisoformat(a['approved_at'][:10])
    if a.get('open_blockers') != [] or a.get('open_major') != []:
        raise ValueError('Open or unspecified blocking/major issues')
    if a.get('publication_scope') != 'approved_for_public_github':
        raise ValueError('Public repository suitability not confirmed')
    if a.get('validation_status') not in ('PASS', 'NOT_APPLICABLE'):
        raise ValueError('Format validation not complete')
    if a['validation_status'] == 'NOT_APPLICABLE' and not nonempty(a.get('validation_na_reason')):
        raise ValueError('Explain why schema validation does not apply')
    payload = {'approval.json': approval_bytes}
    for kind, target in [('xml', None), ('review','review.md'), ('validation','validation.md')]:
        rel = a.get(kind+'_file')
        if not nonempty(rel) or Path(rel).is_absolute(): raise ValueError('Relative source path required')
        p = (approval.parent / rel).resolve()
        if not p.is_relative_to(approval.parent): raise ValueError('Source path escapes approval directory')
        data = p.read_bytes()
        if not data or hashlib.sha256(data).hexdigest() != a.get(kind+'_sha256'):
            raise ValueError('Missing or mismatched approved hash: '+kind)
        if kind == 'xml':
            if p.suffix.lower() not in ('.xml','.gge'): raise ValueError('XML/GGE required')
            ET.fromstring(data, parser=ET.XMLParser(target=NoDTD()))
            target = 'product'+p.suffix.lower()
        payload[target] = data
    attachments = a.get('attachments', [])
    if not isinstance(attachments, list): raise ValueError('attachments must be a list')
    if type(a.get('customer_approval_required')) is not bool:
        raise ValueError('Specify whether customer approval is required')
    has_customer_approval = False
    for i, item in enumerate(attachments, 1):
        if not isinstance(item, dict) or not nonempty(item.get('file')) or not nonempty(item.get('purpose')):
            raise ValueError('Attachment file and purpose are required')
        rel = Path(item['file'])
        p = (approval.parent/rel).resolve()
        if rel.is_absolute() or not p.is_relative_to(approval.parent):
            raise ValueError('Attachment escapes approval directory')
        data = p.read_bytes()
        if not data or hashlib.sha256(data).hexdigest() != item.get('sha256'):
            raise ValueError('Attachment hash mismatch')
        payload['attachments/'+str(i).zfill(3)+'_'+p.name] = data
        has_customer_approval |= item['purpose'] == 'customer_approval'
    if a['customer_approval_required'] and not has_customer_approval:
        raise ValueError('Customer approval evidence missing')
    parent = ROOT / 'products' / project
    if parent.resolve().parent != (ROOT/'products').resolve(): raise ValueError('Unsafe product path')
    parent.mkdir(parents=True,exist_ok=True)
    dest = parent/version
    if dest.exists(): raise FileExistsError('Version already exists; create a new version')
    manifest = {'project_id':project, 'version':version,
                'files':[{'path':n,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in payload.items()]}
    temp = Path(tempfile.mkdtemp(prefix='.staging-',dir=parent))
    try:
        for name,data in payload.items():
            (temp/name).parent.mkdir(parents=True,exist_ok=True)
            (temp/name).write_bytes(data)
        (temp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        temp.rename(dest)
    finally:
        if temp.exists(): shutil.rmtree(temp)
    return dest

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('project'); p.add_argument('version'); p.add_argument('approval',type=Path)
    a=p.parse_args()
    result=archive(a.project,a.version,a.approval)
    print(json.dumps({'path':str(result),'github_status':'PENDING_PUBLICATION',
                      'xsd_checked_by_this_tool':False},ensure_ascii=False))

if __name__=='__main__':
    try: main()
    except (ValueError,OSError,ET.ParseError) as exc: raise SystemExit(str(exc))
