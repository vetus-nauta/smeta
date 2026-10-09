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
    for value,unit in re.findall(r'(\d+(?:[.,]\d+)?)\s*(?:\([^)]*\)\s*)?(м3|m3|м2|m2|см|мм|квт|кв|т|м)\b',text):
        unit={'m3':'м3','m2':'м2'}.get(unit,unit)
        quantities.setdefault(unit,set()).add(str(Decimal(value.replace(',','.')).normalize()))
    groups=re.findall(r'групп[аы]?\s*(?:грунт\w*\s*)?(\d+)',text)
    groups+=re.findall(r'грунт\w*\s*(\d+)\s*групп',text)
    if groups:quantities['soil_group']=set(groups)
    layer_words={'один':'1','одного':'1','одном':'1','два':'2','двух':'2','три':'3','трех':'3','трёх':'3'}
    layers=re.findall(r'(\d+|один|одного|одном|два|двух|три|трех|трёх)\s*сло',text)
    if layers:quantities['layers']={layer_words.get(x,x) for x in layers}
    if re.search(r'половин\w*\s+кирпич|полкирпич|1\s*/\s*2\s*кирпич',text):quantities['brick_thickness']={'0.5'}
    else:
        brick=re.findall(r'(\d+(?:[.,]\d+)?)\s*кирпич',text)
        if brick:quantities['brick_thickness']={str(Decimal(x.replace(',','.')).normalize()) for x in brick}
    return quantities

def _number_ranges(text):
    """Lexical intervals/upper bounds, never a technical-part approval."""
    text=str(text).lower().replace('³','3').replace('²','2').replace(',', '.')
    number=r'(\d+(?:\.\d+)?)';unit=r'(м3|m3|м2|m2|см|мм|квт|кв|т|м)\b'
    ranges={}
    for lo,hi,u in re.findall(number+r'\s*[-–]\s*'+number+r'\s*\)?\s*'+unit,text):
        ranges.setdefault({'m3':'м3','m2':'м2'}.get(u,u),[]).append((Decimal(lo),Decimal(hi)))
    for hi,u in re.findall(r'до\s*'+number+r'\s*'+unit,text):
        ranges.setdefault({'m3':'м3','m2':'м2'}.get(u,u),[]).append((Decimal(0),Decimal(hi)))
    for lo,hi,u in re.findall(r'от\s*'+number+r'\s*до\s*'+number+r'\s*'+unit,text):
        ranges.setdefault({'m3':'м3','m2':'м2'}.get(u,u),[]).append((Decimal(lo),Decimal(hi)))
    return ranges


def _prefix_hits(terms,text):
    words=re.findall(r'[а-яёa-z0-9]+',str(text).lower())
    return [t for t in terms if any(w.startswith(t) for w in words)]


def _numeric_evidence(requested,name):
    actual=_technical_numbers(name);intervals=_number_ranges(name);matches=[];mismatches=[];missing=[]
    for dimension,values in requested.items():
        for value in sorted(values):
            evidence={'dimension':dimension,'dimension_unit':{'brick_thickness':'brick_count','layers':'layer_count','soil_group':'ordinal_group'}.get(dimension,dimension),'requested':value,'candidate_values':sorted(actual.get(dimension,set()))}
            if value in actual.get(dimension,set()) or any(lo<=Decimal(value)<=hi for lo,hi in intervals.get(dimension,[])):
                evidence['interval_match']=value not in actual.get(dimension,set());matches.append(evidence)
            elif dimension not in actual:missing.append(evidence)
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
        focused_rank={}
        if groups['technology']:
            focused=' AND '.join('"'+t+'"*' for t in groups['technology'])
            focused_rows=db.execute("SELECT r.* FROM records_fts f JOIN records r ON r.id=f.rowid WHERE records_fts MATCH ? AND r.kind='norm' ORDER BY bm25(records_fts),r.id LIMIT 300",(focused,)).fetchall()
            focused_rank={r['id']:i+1 for i,r in reversed(list(enumerate(focused_rows)))}
            rows+=focused_rows
        # Material names search the actual resource price records, then norm composition.
        if groups['materials']:
            mq=' OR '.join('"'+t+'"*' for t in groups['materials'])
            resource_codes=[r[0] for r in db.execute('''SELECT DISTINCT r.code FROM records_fts f
               JOIN records r ON r.id=f.rowid WHERE records_fts MATCH ? AND r.kind='price_resource' LIMIT 100''',(mq,))]
            for code in resource_codes:
                rows+=db.execute('''SELECT DISTINCT r.* FROM norm_resources n JOIN records r ON r.id=n.record_id
                    WHERE n.code=? AND r.kind='norm' LIMIT 100''',(code,)).fetchall()
        ranked=[];seen=set();initial_rank={r['id']:i+1 for i,r in reversed(list(enumerate(rows)))}
        for r in rows:
            if r['id'] in seen or (r['namespace'],r['revision']) not in allowed:continue
            seen.add(r['id']);text=(r['name']+' '+(r['body'] or '')).lower()
            resource_text=(' '.join(n[0] for n in db.execute('SELECT attributes_json FROM norm_resources WHERE record_id=?',(r['id'],))).lower() if groups['materials'] else '')
            hits={k:_prefix_hits(ts,text+(' '+resource_text if k=='materials' else '')) for k,ts in groups.items()}
            score=sum(len(ts)*({'technology':4,'materials':3,'description':2}.get(k,1)) for k,ts in hits.items())
            unit_match=None if not unit else unit==r['unit']
            requested_unit=_unit_info(unit);norm_unit=_unit_info(r['unit'])
            compatible=None if not unit else (requested_unit['physical_unit'] is not None and requested_unit['physical_unit']==norm_unit['physical_unit'])
            if compatible:score+=6
            elif unit:score-=12
            composition_hits=_prefix_hits(groups['technology'],r['body'] or '')
            name_technology_hits=_prefix_hits(groups['technology'],r['name'])
            name_hits=_prefix_hits(terms,r['name'])
            score+=4*len(name_hits)+60/(1+initial_rank[r['id']])
            if r['id'] in focused_rank:score+=60/(1+focused_rank[r['id']])
            # Numeric/layer conditions already have dimensional scoring below.
            # Do not count word-form numerals again as installation technology.
            operation_hits=[t for t in name_technology_hits if t not in ('один','одного','одном','два','двух','три','трех','трёх','слоя','слоев','слоёв') and not t.isdigit()]
            score+=12*len(operation_hits)
            # Object/purpose from the description matters as much as installation.
            # Retrieval ranking only, never applicability evidence.
            score+=12*len(_prefix_hits(groups['description'],r['name']))
            conflicts=selection_conflicts({'description':description,'technology':technology,'materials':materials,'technical_conditions':technical_conditions},dict(r))
            score-=120*len(conflicts)
            score-=5*sum(t not in hits['technology'] for t in groups['technology'])
            numbers=_numeric_evidence(requested_numbers,r['name'])
            score+=20*len(numbers['matches'])-30*len(numbers['mismatches'])-8*len(numbers['missing_in_candidate_name'])
            ranked.append((score,r,hits,unit_match,numbers,compatible,norm_unit,composition_hits,name_technology_hits))
        for score,r,hits,unit_match,numbers,compatible,norm_unit,composition_hits,name_technology_hits in sorted(ranked,key=lambda x:(-x[0],x[1]['namespace'],x[1]['revision'],x[1]['code']))[:limit]:
            e=_evidence(db,r);e['rank_evidence']={'score':score,'first_pass_rank':initial_rank[r['id']],'matched_prefix_tokens':hits,'requested_unit':unit,
              'unit_matches':unit_match,'exact_norm_unit_matches':unit_match,'physical_unit_compatible':compatible,
              'norm_unit':norm_unit,'composition_technology_hits':composition_hits,'name_technology_hits':name_technology_hits,'numeric_conditions':numbers,'technical_conditions_verified':False,
              'unit_policy':'Exact normative unit and physical dimensional compatibility are separate. Factor reported explicitly; mismatched dimensions never converted.'}
            e['rank_evidence']['negative_requirement_conflicts']=selection_conflicts({'description':description,'technology':technology,'materials':materials,'technical_conditions':technical_conditions},e)
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


def context_literals(record):
    """Verbatim normative section labels, not inferred applicability."""
    return re.findall(r'\b[А-ЯЁ][А-ЯЁ0-9 (),;/\-]{10,}[А-ЯЁ0-9]\b',record['body'])


def selection_conflicts(source,record):
    """Conservative negative requirements, never evidence of applicability.

    Scope restrictions are taken only from verbatim normative headings. Primary
    installation methods are taken from the title and work body, not resources or
    an auxiliary protective layer. A matching token never grants approval.
    """
    original=' '.join(str(source.get(k,'')) for k in ('description','technology','materials','technical_conditions')).lower()
    headings=' '.join(context_literals(record)).lower()
    restrictions={
      'nuclear_power':r'атомн\w*\s+электростанц|\bаэс\b',
      'hydraulic_structure':r'гидротехническ',
      'water_management_earthwork':r'водохозяйственн|(?:устройство|строительство)\s+каналов|дамб\s+обвалован',
      'sliding_formwork':r'скользящ\w*\s+опалуб',
      'metro':r'метрополитен',
      'roofing':r'кровл|крыш',
      'bridge_structure':r'мост(?:ы|ов|а|у|ах|ами)?\b|путепровод',
      'underwater_work':r'подводн',
      'offshore_structure':r'морск\w*\s+(?:сооружен|гидротех)',
    }
    conflicts=[]
    for kind,pattern in restrictions.items():
        if re.search(pattern,str(record.get('body','')).lower()) and not re.search(pattern,original):
            conflicts.append({'kind':'MISSING_SPECIALIZED_SCOPE','requirement':kind,'normative_text':record.get('body',''),'reason':'Original source does not establish the explicitly named specialized scope'})
    # Flame fusing and gluing the main roll layer are different operations.
    # Mastic used for gravel protection is not the installation of the roll layer.
    main_source=' '.join(str(source.get(k,'')) for k in ('description','technology')).lower()
    body=record.get('body','').lower()
    fused=r'наплав|подплав|газопламенн'
    glued=r'(?:наклей|приклей|наклеив|приклеив)\w*[^.]{0,100}(?:рулон|ковр)[^.]{0,100}(?:мастик|кле)'
    if re.search(fused,main_source) and re.search(glued,body) and not re.search(fused,body):
        conflicts.append({'kind':'PRIMARY_METHOD_CONTRADICTION','source_method':'flame_fusing','norm_method':'adhesive_roll_installation','reason':'Explicit source fusing cannot be replaced by mastic gluing of the main roll layer'})
    if re.search(r'оклееч|наклей|приклей|приклеив|наклеив',main_source) and re.search(r'насухо|без\s+(?:приклеив|наклеив|мастик)',record.get('name','').lower()):
        conflicts.append({'kind':'PRIMARY_METHOD_CONTRADICTION','source_method':'adhesive_installation','norm_method':'dry_laying','reason':'Explicit adhesive installation cannot be replaced by dry laying'})
    # Opposed orientation and roll-vs-applied coating are explicit negative
    # work requirements. Their absence never establishes positive applicability.
    title=record.get('name','').lower()
    for source_direction,norm_direction in ((r'вертикальн',r'горизонтальн'),(r'горизонтальн',r'вертикальн')):
        if re.search(source_direction,original) and not re.search(norm_direction,original) and re.search(norm_direction,title) and not re.search(source_direction,title):
            conflicts.append({'kind':'PRIMARY_ORIENTATION_CONTRADICTION','normative_text':record.get('name',''),'reason':'Explicit source and norm orientations are opposed'})
    if re.search(r'рулон',original) and not re.search(r'акрил|эластичн[^.]{0,40}покрыт',original) and re.search(r'акрил',title) and not re.search(r'рулон',body):
        conflicts.append({'kind':'PRIMARY_METHOD_CONTRADICTION','source_method':'roll_material_installation','norm_method':'applied_acrylic_coating','reason':'Applied acrylic coating cannot replace explicitly specified roll material'})
    # Explicit dimensional restrictions in a norm title require a project value.
    # Quantity of the billed work is deliberately not used as that parameter.
    required_numbers=_technical_numbers(record.get('name',''))
    original_numbers=_technical_numbers(original)
    for dimension,values in required_numbers.items():
        if dimension not in original_numbers:
            conflicts.append({'kind':'MISSING_NORMATIVE_PARAMETER','dimension':dimension,'candidate_values':sorted(values),'reason':'An explicit norm-title parameter has no original project value; billed quantity is not that parameter'})
    # A named supplementary protective construction is not implied by generic
    # roll insulation. Absence is a blocker; matching a term grants no approval.
    title=record.get('name','').lower()
    protection={
      'protective_sheet':r'защитн[^.]{0,100}(?:асбестоцементн|лист)',
      'protective_membrane':r'защитн[^.]{0,60}мембран',
      'protective_slab':r'защитн[^.]{0,100}(?:железобетонн|плит)',
    }
    source_protection={'protective_sheet':r'асбестоцементн|лист', 'protective_membrane':r'мембран','protective_slab':r'железобетонн|плит'}
    for kind,pattern in protection.items():
        if re.search(pattern,title) and not re.search(source_protection[kind],original):
            conflicts.append({'kind':'MISSING_SUPPLEMENTARY_CONSTRUCTION','requirement':kind,'reason':'The named supplementary protective construction is not specified in the original work'})
    return conflicts
