#!/usr/bin/env python3
"""Local office registry. Structural checks do not approve estimates."""
import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def db():
    c = sqlite3.connect(ROOT / 'office.sqlite3')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS projects (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
      path TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS tasks (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, role TEXT NOT NULL,
      assignment TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sources (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
      checked_on TEXT, status TEXT, metadata_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS artifacts (
      project_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
      recorded_at TEXT NOT NULL, PRIMARY KEY(project_id,path));
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, project_id TEXT,
      action TEXT NOT NULL, details TEXT NOT NULL);
    ''')
    return c

def project_path(code):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', code):
        raise ValueError('Project ID: 1–64 Latin letters, digits, _ or -')
    p = ROOT / 'projects' / code
    if p.resolve().parent != (ROOT / 'projects').resolve():
        raise ValueError('Project path escapes projects directory')
    return p

def index_project(c, p):
    meta = json.loads((p / 'project.json').read_text())
    if meta['id'] != p.name:
        raise ValueError('Project ID and directory do not match')
    c.execute('INSERT OR REPLACE INTO projects VALUES (?,?,?,?,?)',
              (meta['id'], meta['name'], meta['status'], str(p), now()))
    return meta

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    n = sub.add_parser('new'); n.add_argument('id'); n.add_argument('--name', required=True)
    sub.add_parser('list')
    sub.add_parser('index-sources')
    for name in ('check', 'manifest'):
        cmd = sub.add_parser(name); cmd.add_argument('id')
    t = sub.add_parser('task')
    t.add_argument('id'); t.add_argument('--task-id', required=True)
    t.add_argument('--role', required=True); t.add_argument('--assignment', required=True)
    a = ap.parse_args()
    with db() as c:
        if a.command == 'init':
            for meta in sorted((ROOT / 'projects').glob('*/project.json')):
                index_project(c, meta.parent)
            print(ROOT / 'office.sqlite3')
        elif a.command == 'new':
            p = project_path(a.id)
            p.mkdir(parents=True, exist_ok=False)
            for folder in ('input', 'work', 'review', 'release', 'handoffs'):
                (p / folder).mkdir()
            data = {'id': a.id, 'name': a.name, 'status': 'DRAFT', 'created_at': now(),
                    'region': None, 'price_date': None, 'method': None,
                    'normative_base': None, 'tax_basis': None, 'educational': False}
            (p / 'project.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
            for src, dst in [('project-passport.md', 'passport.md'),
                             ('release-decision.md', 'release-decision.md'),
                             ('review.md', 'review/review.md')]:
                shutil.copyfile(ROOT / 'templates' / src, p / dst)
            for src in (ROOT / 'templates').glob('*.csv'):
                shutil.copyfile(src, p / 'work' / src.name)
            index_project(c, p)
            c.execute('INSERT INTO events(occurred_at,project_id,action,details) VALUES (?,?,?,?)',
                      (now(), a.id, 'CREATE', a.name))
            print(p)
        elif a.command == 'list':
            for row in c.execute('SELECT id,name,status,path FROM projects ORDER BY id'):
                print('\t'.join(row))
        elif a.command == 'index-sources':
            count = 0
            seen = set()
            for path in sorted((ROOT / 'sources').glob('*-sources.json')):
                data = json.loads(path.read_text())
                entries = data if isinstance(data, list) else data['sources']
                for s in entries:
                    if s['id'] in seen:
                        raise ValueError('Duplicate source ID: '+s['id'])
                    seen.add(s['id'])
                    if not s['url'].startswith(('https://', 'http://')):
                        raise ValueError('Invalid source URL: '+s['id'])
                    c.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?)',
                              (s['id'], s['title'], s['url'], s.get('checked_on') or s.get('checked_at'),
                               s.get('status'), json.dumps(s, ensure_ascii=False)))
                    count += 1
            print(json.dumps({'indexed_sources': count}, ensure_ascii=False))
        elif a.command == 'task':
            p = project_path(a.id); index_project(c, p)
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',
                      (a.task_id, a.id, a.role, a.assignment, 'TODO', now()))
            print(a.task_id)
        elif a.command == 'check':
            p = project_path(a.id); meta = index_project(c, p)
            missing = [k for k in ('region','price_date','method','normative_base','tax_basis')
                       if not meta.get(k)]
            required = ['passport.md', 'review/review.md', 'release-decision.md']
            missing_files = [f for f in required if not (p / f).is_file()]
            print(json.dumps({'project': a.id, 'missing_metadata': missing,
                              'missing_files': missing_files,
                              'technical_structure_ok': not missing_files,
                              'professional_approval': 'NOT_EVALUATED'}, ensure_ascii=False, indent=2))
            if missing or missing_files:
                raise SystemExit(2)
        elif a.command == 'manifest':
            p = project_path(a.id); index_project(c, p)
            files = []
            for f in sorted((p / 'release').rglob('*')):
                if not f.is_file():
                    continue
                if not f.resolve().is_relative_to(p.resolve()) or f.is_symlink():
                    raise ValueError('Symlinks in release are not indexed')
                rel = str(f.relative_to(p))
                digest = hashlib.sha256(f.read_bytes()).hexdigest()
                files.append({'path': rel, 'sha256': digest, 'bytes': f.stat().st_size})
            if not files:
                raise ValueError('No release files; a manifest cannot be empty')
            stamp = now()
            manifest = {'project_id': a.id, 'created_at': stamp, 'approval': 'NOT_EVALUATED', 'files': files}
            (p / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
            for f in files:
                c.execute('INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?)',
                          (a.id, f['path'], f['sha256'], stamp))
            c.execute('INSERT INTO events(occurred_at,project_id,action,details) VALUES (?,?,?,?)',
                      (stamp, a.id, 'MANIFEST', str(len(files))))
            print(p / 'manifest.json')

if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        raise SystemExit(str(exc))
