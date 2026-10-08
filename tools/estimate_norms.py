"""Evidence-bearing candidate retrieval from the existing normative database, read only.

Candidates are never an approval to use a norm. No latest-revision substitution.
"""
import json
import re
import sqlite3
import importlib.util
from decimal import Decimal
from pathlib import Path

BRIDGE = Path('/ai-data/research/estimates/grand-course/PRACTICE-AND-NORMATIVE-BRIDGE.json')
STOP = {'для', 'при', 'или', 'как', 'над', 'под', 'без', 'the', 'and', 'with'}

def tokens(text):
    # Prefix stemming is retrieval only: original words remain in evidence.
    return sorted({w[:max(4, len(w)-3)] if len(w)>6 else w
                   for w in re.findall(r'[а-яёa-z0-9]+', str(text).lower())
                   if len(w)>2 and w not in STOP})

def _connect():
    spec=importlib.util.spec_from_file_location('ai_station_storage','/opt/ai-station/storage.py')
    storage=importlib.util.module_from_spec(spec);spec.loader.exec_module(storage)
    cfg=storage.policy()
    staging=storage.resolve_storage('estimates','staging',cfg=cfg)
    path=Path(json.loads(BRIDGE.read_text())['canonical_existing_database']).absolute()
    if path.is_symlink() or path.resolve(strict=True)!=path:
        raise storage.StorageError('FAIL CLOSED: symlinked normative source')
    if not path.is_relative_to(staging):
        raise storage.StorageError('FAIL CLOSED: normative source outside resolved staging')
    storage.ingestion_allowed(path,cfg)
    storage.mount_guard(cfg,'HOT',False)
    registry_path=Path(cfg['registry'])
    with sqlite3.connect(registry_path.as_uri()+'?mode=ro',uri=True) as registry:
        registrations=registry.execute("SELECT canonical_path FROM objects WHERE project='estimates' AND lower(data_class)='staging'").fetchall()
    if not any(path==Path(r[0]) or path.is_relative_to(Path(r[0])) for r in registrations):
        raise storage.StorageError('FAIL CLOSED: normative staging source not registered')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db

def catalog():
    with _connect() as db:
        scopes=[dict(r) for r in db.execute('''SELECT r.namespace,r.revision,r.kind,
          COUNT(*) AS records,s.id AS source_id,s.authority,s.status AS source_status,
          s.published_at,s.effective_at,s.url,s.sha256
          FROM records r JOIN sources s ON s.id=r.source_id
          GROUP BY r.namespace,r.revision,r.kind,r.source_id ORDER BY r.namespace,r.revision''')]
    return {'scopes':scopes,'applicability':'NOT_VERIFIED','database_origin':str(BRIDGE)}

def _scopes(namespace,revision,source_metadata,calculation_period):
    meta=source_metadata or {}
    if namespace and meta.get('namespace') and namespace!=meta['namespace']:return []
    if revision and meta.get('revision') and revision!=meta['revision']:return []
    namespace=namespace or meta.get('namespace')
    revision=revision or meta.get('revision')
    scopes=catalog()['scopes']
    eligible=[s for s in scopes if (not namespace or s['namespace']==namespace)
              and (not revision or s['revision']==revision)
              and (not meta.get('source_id') or s['source_id']==meta['source_id'])]
    # An unrepresented amendment request must not silently turn into d9/d19.
    amendment=meta.get('amendments')
    if amendment is not None and not revision:
        if isinstance(amendment,(list,tuple)): amendment=max(amendment) if amendment else 0
        eligible=[s for s in eligible if s['revision'].endswith('-d'+str(amendment))]
    if calculation_period:
        # Effective-before date is necessary, never sufficient, for applicability.
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',calculation_period):
            raise ValueError('calculation_period must be ISO date YYYY-MM-DD')
        eligible=[s for s in eligible if s['effective_at'] and s['effective_at'][:10]<=calculation_period]
    return eligible

def _evidence(db,record):
    result=dict(record)
    result['source']=dict(db.execute('SELECT * FROM sources WHERE id=?',(record['source_id'],)).fetchone())
    result['resources']=[dict(r) for r in db.execute('SELECT * FROM norm_resources WHERE record_id=? ORDER BY ordinal',(record['id'],))]
    result['prices']=[dict(r) for r in db.execute('SELECT * FROM price_components WHERE record_id=? ORDER BY ordinal,field',(record['id'],))]
    result['source_identity']={k:record[k] for k in ('id','source_id','content_hash','namespace','revision','kind','code')}
    result['applicability']='NOT_VERIFIED'
    return result

def exact_lookup(namespace,revision,code,kind='norm'):
    if any(not isinstance(x,str) or not x.strip() for x in (namespace,revision,code,kind)):
        raise ValueError('Exact namespace, revision, kind and code are mandatory')
    with _connect() as db:
        rows=db.execute('SELECT * FROM records WHERE namespace=? AND revision=? AND kind=? AND code=?',
                        (namespace,revision,kind,code)).fetchall()
        return {'status':'FOUND' if rows else 'NOT_AVAILABLE','records':[_evidence(db,r) for r in rows],
                'applicability':'NOT_VERIFIED'}

def _unit_info(unit):
    raw=str(unit or '').strip().replace('²','2').replace('³','3')
    physical_alias={'m2':'м2','m3':'м3','m':'м','piece':'шт','t':'т','kg':'кг'}
    raw=physical_alias.get(raw,raw)
    match=re.fullmatch(r'(?:(1000|100|10|1)\s*)?(м2|м3|м|т|кг|шт\.?|маш\.-ч|чел\.-ч)',raw)
    if not match:return {'physical_unit':None,'norm_factor':None,'raw_unit':unit}
    physical={'м2':'m2','м3':'m3','м':'m','т':'t','кг':'kg','шт':'piece','шт.':'piece','маш.-ч':'machine_hour','чел.-ч':'person_hour'}[match.group(2)]
    return {'physical_unit':physical,'norm_factor':match.group(1) or '1','raw_unit':unit}

def _technical_numbers(text):
    text=str(text).lower().replace('³','3').replace('²','2')
    quantities={}
    for value,unit in re.findall(r'(\d+(?:[.,]\d+)?)\s*(?:\([^)]*\)\s*)?(м3|m3|м2|m2|см|мм|квт|кв|т)\b',text):
        unit={'m3':'м3','m2':'м2'}.get(unit,unit)
        quantities.setdefault(unit,set()).add(str(Decimal(value.replace(',','.')).normalize()))
    groups=re.findall(r'групп[аы]?\s*(?:грунт\w*\s*)?(\d+)',text)
    groups+=re.findall(r'грунт\w*\s*(\d+)\s*групп',text)
    if groups:quantities['soil_group']=set(groups)
    return quantities

def _numeric_evidence(requested,name):
    actual=_technical_numbers(name);matches=[];mismatches=[];missing=[]
    for dimension,values in requested.items():
        for value in sorted(values):
            evidence={'dimension':dimension,'requested':value,'candidate_values':sorted(actual.get(dimension,set()))}
            if dimension not in actual:missing.append(evidence)
            elif value in actual[dimension]:matches.append(evidence)
            else:mismatches.append(evidence)
    return {'matches':matches,'mismatches':mismatches,'missing_in_candidate_name':missing,
            'authority':'Lexical numeric candidate ranking only; technical applicability not verified.'}

def search_candidates(description,technology='',materials='',unit='',technical_conditions='',
                      namespace=None,revision=None,source_metadata=None,calculation_period=None,limit=10):
    """Rank descriptions, operation bodies and material resource identities.

    Source metadata accepts namespace, revision, source_id, amendments. ISO calculation
    date filters known effective dates, but does not assert legal applicability or expiry.
    """
    if not 1<=limit<=50: raise ValueError('limit must be between 1 and 50')
    groups={k:tokens(v) for k,v in [('description',description),('technology',technology),
            ('materials',materials),('technical_conditions',technical_conditions)]}
    requested_numbers=_technical_numbers(' '.join(str(x) for x in (description,technology,technical_conditions)))
    scopes=_scopes(namespace,revision,source_metadata,calculation_period)
    result={'status':'CANDIDATES' if scopes else 'NOT_AVAILABLE','scopes':scopes,'candidates':[],
            'applicability':'NOT_VERIFIED','calculation_period':calculation_period,
            'revision_policy':'No implicit latest revision; inspect candidate source/effective date and project applicability.',
            'missing_authority':['Project technology and technical parts','Revision expiry/supersession not represented'],
            'retrieval_limits':{'text_candidates':3000,'resource_codes':100,'norms_per_resource':100},
            'search_coverage':'Bounded candidate retrieval, not proof that no alternative norm exists.'}
    if not scopes:return result
    terms=sorted({t for ts in groups.values() for t in ts})
    if not terms:raise ValueError('At least one descriptive search token is required')
    allowed={(s['namespace'],s['revision']) for s in scopes if s['kind']=='norm'}
    with _connect() as db:
        query=' OR '.join('"'+t+'"*' for t in terms)
        rows=db.execute('''SELECT r.* FROM records_fts f JOIN records r ON r.id=f.rowid
            WHERE records_fts MATCH ? AND r.kind='norm' ORDER BY bm25(records_fts),r.id LIMIT 3000''',(query,)).fetchall()
        # Material names search the actual resource price records, then norm composition.
        if groups['materials']:
            mq=' OR '.join('"'+t+'"*' for t in groups['materials'])
            resource_codes=[r[0] for r in db.execute('''SELECT DISTINCT r.code FROM records_fts f
               JOIN records r ON r.id=f.rowid WHERE records_fts MATCH ? AND r.kind='price_resource' LIMIT 100''',(mq,))]
            for code in resource_codes:
                rows+=db.execute('''SELECT DISTINCT r.* FROM norm_resources n JOIN records r ON r.id=n.record_id
                    WHERE n.code=? AND r.kind='norm' LIMIT 100''',(code,)).fetchall()
        ranked=[];seen=set()
        for r in rows:
            if r['id'] in seen or (r['namespace'],r['revision']) not in allowed:continue
            seen.add(r['id']);text=(r['name']+' '+(r['body'] or '')).lower()
            resource_text=(' '.join(n[0] for n in db.execute('SELECT attributes_json FROM norm_resources WHERE record_id=?',(r['id'],))).lower() if groups['materials'] else '')
            hits={k:[t for t in ts if t in text or k=='materials' and t in resource_text] for k,ts in groups.items()}
            score=sum(len(ts)*({'technology':4,'materials':3,'description':2}.get(k,1)) for k,ts in hits.items())
            unit_match=None if not unit else unit==r['unit']
            requested_unit=_unit_info(unit);norm_unit=_unit_info(r['unit'])
            compatible=None if not unit else (requested_unit['physical_unit'] is not None and requested_unit['physical_unit']==norm_unit['physical_unit'])
            if compatible:score+=6
            elif unit:score-=12
            composition_hits=[t for t in groups['technology'] if t in (r['body'] or '').lower()]
            name_technology_hits=[t for t in groups['technology'] if t in r['name'].lower()]
            score+=5*len(name_technology_hits)
            score-=5*sum(t not in text for t in groups['technology'])
            numbers=_numeric_evidence(requested_numbers,r['name'])
            score+=20*len(numbers['matches'])-30*len(numbers['mismatches'])-8*len(numbers['missing_in_candidate_name'])
            ranked.append((score,r,hits,unit_match,numbers,compatible,norm_unit,composition_hits,name_technology_hits))
        for score,r,hits,unit_match,numbers,compatible,norm_unit,composition_hits,name_technology_hits in sorted(ranked,key=lambda x:(-x[0],x[1]['namespace'],x[1]['revision'],x[1]['code']))[:limit]:
            e=_evidence(db,r);e['rank_evidence']={'score':score,'matched_prefix_tokens':hits,'requested_unit':unit,
              'unit_matches':unit_match,'exact_norm_unit_matches':unit_match,'physical_unit_compatible':compatible,
              'norm_unit':norm_unit,'composition_technology_hits':composition_hits,'name_technology_hits':name_technology_hits,'numeric_conditions':numbers,'technical_conditions_verified':False,
              'unit_policy':'Exact normative unit and physical dimensional compatibility are separate. Factor reported explicitly; mismatched dimensions never converted.'}
            result['candidates'].append(e)
    if not result['candidates']:result['status']='NO_CANDIDATES'
    return result

def calculation_record(source_identity):
    """Resolve immutable identity before a caller calculates; detect stale/tampered selection."""
    required=('id','source_id','content_hash','namespace','revision','kind','code')
    if any(k not in source_identity for k in required):raise ValueError('Complete source identity required')
    with _connect() as db:
        r=db.execute('SELECT * FROM records WHERE id=?',(source_identity['id'],)).fetchone()
        if r is None or any(r[k]!=source_identity[k] for k in required):
            raise ValueError('Source identity mismatch')
        return _evidence(db,r)


def norm_to_calculation(record):
    """Adapt immutable actual norm evidence; unresolved project resources stay missing."""
    actual=calculation_record(record['source_identity'])
    if actual['kind']!='norm':raise ValueError('Calculation requires a norm record')
    unit=actual.get('unit','').strip().replace('²','2').replace('³','3')
    match=re.fullmatch(r'(?:(1000|100|10|1)\s+)?(м2|м3|м|т|кг|шт\.?|маш\.-ч|чел\.-ч)',unit)
    missing=[]
    factor=match.group(1) or '1' if match else None
    physical={'м2':'m2','м3':'m3','м':'m','т':'t','кг':'kg','шт':'piece','шт.':'piece','маш.-ч':'machine_hour','чел.-ч':'person_hour'}.get(match.group(2)) if match else None
    if not match:missing.append('Normative unit multiplier unresolved: '+unit)
    prices={p['field']:p['value_decimal'] for p in actual['prices'] if p['ordinal']==1}
    components={target:prices.get(source) for source,target in [('Salary','labour'),('Machines','machines'),('SalaryMach','machinist'),('Materials','materials')]}
    for k,v in components.items():
        if v is None:missing.append('Missing actual price component: '+k)
    resources=[]
    with _connect() as db:
        for resource in actual['resources']:
            code=resource['code'];attrs=json.loads(resource['attributes_json'])
            if code.startswith('1-100-'):continue # Duplicate labour aggregate representation.
            resource_unit=attrs.get('MeasureUnit')
            if not resource_unit and code in ('1','2'):resource_unit='чел.-ч'
            if not resource_unit:
                rows=db.execute('SELECT DISTINCT unit FROM records WHERE revision=? AND code=? AND kind=?',(actual['revision'],code,'price_resource')).fetchall()
                units={r[0] for r in rows if r[0]}
                if len(units)==1:resource_unit=units.pop()
            included=resource['tag']!='AbstractResource'
            quantity=resource['quantity_decimal']
            if quantity is None:missing.append('Project/resource quantity unresolved: '+code+' = '+str(resource['quantity_raw']))
            if not resource_unit:missing.append('Resource unit unresolved: '+code)
            if not included:missing.append('Uncounted resource requires project selection and actual price: '+code)
            resources.append({'id':code+':'+str(resource['ordinal']),'code':code,'unit':resource_unit,
              'quantity_per_norm':quantity,'quantity_raw':resource['quantity_raw'],'included':included,
              'external_price':None,'evidence':{'source_identity':actual['source_identity'],'attributes':attrs}})
    return {'code':actual['code'],'namespace':actual['namespace'],'revision':actual['revision'],
      'unit':actual['unit'],'source':actual['source']['title']+'; '+actual['source_id']+'; '+(actual['source'].get('url') or ''),
      'physical_unit':physical,'norm_factor':factor,'components':components,'resources':resources,
      'evidence':{'source_identity':actual['source_identity'],'source':actual['source'],'locator':actual['locator']},
      'applicability_confirmed':False,'missing':missing,
      'status':'NEEDS_SOURCE_DATA' if missing else 'BASE_COMPONENTS_AVAILABLE_APPLICABILITY_NOT_VERIFIED'}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('description',nargs='?');p.add_argument('--namespace');p.add_argument('--revision');p.add_argument('--unit',default='');p.add_argument('--technology',default='');p.add_argument('--materials',default='');p.add_argument('--period');p.add_argument('--catalog',action='store_true');a=p.parse_args()
    print(json.dumps(catalog() if a.catalog else search_candidates(a.description or '',a.technology,a.materials,a.unit,namespace=a.namespace,revision=a.revision,calculation_period=a.period),ensure_ascii=False,indent=2))
