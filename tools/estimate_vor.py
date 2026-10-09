"""Read headered multi-sheet VOR without interpreting document data as authority.

All source work rows stay in lines or rejected_rows. Source normalized units are
converted once to physical quantities; normative factor is chosen later.
"""
import re
from decimal import Decimal, InvalidOperation

MAX_WORK_ROWS = 5000
ALIASES = {
 'description': {'наименованиеработ', 'описаниеработ', 'содержаниеработ', 'работа', 'description', 'наименованиеихарактеристикаработ', 'наименованиеработизатрат'},
 'unit': {'едизм', 'единицаизмерения', 'единица', 'unit', 'единицыизмерения'},
 'value': {'количество', 'колво', 'объем', 'объём', 'quantity', 'количествообъемработ', 'колвообъемработ', 'объемработ', 'объёмработ'},
 'technology': {'технология', 'technology'}, 'materials': {'материалы', 'materials'},
 'technical_conditions': {'техническиеусловия', 'technicalconditions'},
 'source_id': {'пп', 'порядковыйномер', 'номер', 'id'},
}
PARAM_ALIASES = {'namespace':'namespace', 'revision':'revision', 'scope':'scope', 'basescope':'scope',
 'calculation_scope':'calculation_scope', 'basedirectonly':'base_direct_only', 'base_direct_only':'base_direct_only',
 'period':'calculation_period', 'calculation_period':'calculation_period', 'датацен':'calculation_period',
 'регион':'region', 'region':'region', 'project_id':'project_id', 'проект':'project_id',
 'редакция':'revision', 'нормативнаяредакция':'revision', 'нормативнаябаза':'namespace'}

def normalized(value):
 return re.sub(r'[^а-яёa-z]', '', str(value).lower())

def populated(cell):
 return cell is not None and cell.get('value') is not None and str(cell['value']).strip() != ''

def rows_of(sheet):
 rows={}
 for cell in sheet.get('cells',[]):
  coord=re.fullmatch(r'([A-Z]+)([1-9][0-9]*)',cell['cell'])
  if not coord:raise ValueError('Invalid cell coordinate')
  rows.setdefault(int(coord[2]),{})[coord[1]]=cell
 return rows

def unit_value(unit,value):
 raw=re.sub(r'\s+','',str(unit).lower()).replace('²','2').replace('³','3')
 aliases={'м.п.':'м','мп':'м','пог.м':'м','погм':'м','pcs':'шт','piece':'шт','m':'м','m2':'м2','m3':'м3','kg':'кг','t':'т'}
 raw=aliases.get(raw,raw)
 match=re.fullmatch(r'(\d+(?:[.,]\d+)?)?(м3|м2|м|шт\.?|т|кг|чел\.-ч|маш\.-ч)',raw)
 if not match:raise ValueError('Unsupported or ambiguous physical unit: '+str(unit))
 try:
  factor=Decimal((match[1] or '1').replace(',','.'));number=Decimal(str(value).replace('\u00a0','').replace(' ','').replace(',','.'))
 except InvalidOperation:raise ValueError('Quantity is not a known decimal')
 if isinstance(value,bool) or not factor.is_finite() or factor<=0 or not number.is_finite() or number<0:
  raise ValueError('Quantity/factor invalid; unknown is not zero')
 physical={'м3':'m3','м2':'m2','м':'m','шт':'piece','шт.':'piece','т':'t','кг':'kg','чел.-ч':'person_hour','маш.-ч':'machine_hour'}[match[2]]
 return str(number*factor),physical,str(factor)

def explicit_section(text):
 return bool(re.match(r'^\s*(?:раздел|глава|секция|тип|section)\b',str(text),re.I))

def explicit_total(text):
 return bool(re.match(r'^\s*(?:итого|всего\s+(?:по|работ|затрат)|подытог|subtotal|total)(?:\b|\s|:)',str(text),re.I))

def extract_vor(inspection):
 """Return existing internal schema, traceable accepted/rejected rows and gaps."""
 files=inspection.get('files',[]); params={};metadata=[];missing=[];sections=[];lines=[];rejected=[];row_registry=[]
 def gap(message):
  if message not in missing:missing.append(message)
 # Metadata is gathered across all sheets before any work row is interpreted.
 for fi,file in enumerate(files):
  for si,sheet in enumerate(file.get('sheets',[])):
   if normalized(sheet['sheet']) not in {'параметры','parameters','метаданные','metadata'}:continue
   for row,cells in sorted(rows_of(sheet).items()):
    if not populated(cells.get('A')) or not populated(cells.get('B')):continue
    raw=str(cells['A']['value']).strip();key=PARAM_ALIASES.get(raw.lower(),PARAM_ALIASES.get(normalized(raw)))
    if not key:continue
    record={'file':file['name'],'source_hash':file['sha256'],'sheet':sheet['sheet'],'row':row,'key':key,'value':cells['B']['value'],'key_cell':cells['A']['cell'],'value_cell':cells['B']['cell']};metadata.append(record)
    if key in params and params[key]!=record['value']:gap('Conflicting source metadata: '+key);params[key]=None
    elif key not in params:params[key]=record['value']
 for key in ('namespace','revision','scope','calculation_scope','calculation_period','region','project_id'):
  if key in params and params[key] is not None and (not isinstance(params[key],str) or not params[key].strip()):
   gap('Invalid typed source metadata: '+key);params[key]=None
 work_rows=0
 for fi,file in enumerate(files):
  for si,sheet in enumerate(file.get('sheets',[])):
   if normalized(sheet['sheet']) in {'параметры','parameters','метаданные','metadata'}:continue
   rows=rows_of(sheet);columns=None;section=None;header_seen=False;header_row=None
   formula_status={x['cell']:x.get('status') for x in sheet.get('formulas',[])}
   for row,cells in sorted(rows.items()):
    matches={k:[col for col,cell in cells.items() if populated(cell) and normalized(cell['value']) in names] for k,names in ALIASES.items()}
    if sum(bool(matches[k]) for k in ('description','unit','value'))>=2:
     if not all(len(matches[k])==1 for k in ('description','unit','value')):
      columns=None;reason='Ambiguous or incomplete VOR header';gap(file['name']+'/'+sheet['sheet']+':'+str(row)+': '+reason)
      rejected.append({'kind':'HEADER','reason':reason,'file':file['name'],'sheet':sheet['sheet'],'row':row,'cells':cells});continue
     columns={k:v[0] for k,v in matches.items() if len(v)==1};header_seen=True;header_row=row
     row_registry.append({'file':file['name'],'sheet':sheet['sheet'],'row':row,'kind':'HEADER','columns':columns});continue
    if not columns:
     texts=[str(c['value']) for c in cells.values() if populated(c)]
     if not texts:continue
     heading=next((t for t in texts if explicit_section(t)),None)
     if heading:
      section={'id':f'{file["sha256"][:16]}:f{fi}:s{si}:section:{row}','title':heading,'file':file['name'],'source_hash':file['sha256'],'sheet':sheet['sheet'],'row':row,'cells':cells};sections.append(section);row_registry.append(dict(section,kind='SECTION'));continue
     unclassified={'file':file['name'],'source_hash':file['sha256'],'sheet':sheet['sheet'],'row':row,'kind':'UNCLASSIFIED_NO_HEADER','reason':'No active unambiguous header; no quantity inferred','cells':cells}
     row_registry.append(unclassified);rejected.append(unclassified)
     continue
    selected={k:cells.get(col) for k,col in columns.items()};desc=selected.get('description');unit=selected.get('unit');amount=selected.get('value')
    texts=[str(c['value']) for c in cells.values() if populated(c)]
    # A multirow header may continue with numbered logical graphs, not work.
    if row==header_row+1 and all(populated(selected.get(k)) and type(selected[k]['value']) is int and 1<=selected[k]['value']<=30 for k in ('description','unit','value')):
     row_registry.append({'file':file['name'],'sheet':sheet['sheet'],'row':row,'kind':'HEADER_NUMBERS','cells':cells});continue
    label=str(desc['value']) if populated(desc) else next((t for t in texts if explicit_section(t)),None)
    if label and explicit_total(label):
     row_registry.append({'file':file['name'],'sheet':sheet['sheet'],'row':row,'kind':'TOTAL','cells':cells});continue
    merged_heading=any(re.fullmatch(r'[A-Z]+%s:[A-Z]+%s'%(row,row),r) for r in sheet.get('merged',[]))
    if label and not populated(unit) and not populated(amount) and (explicit_section(label) or merged_heading):
     section={'id':f'{file["sha256"][:16]}:f{fi}:s{si}:section:{row}','title':label,'file':file['name'],'source_hash':file['sha256'],'sheet':sheet['sheet'],'row':row,'cells':cells};sections.append(section)
     row_registry.append(dict(section,kind='SECTION'));continue
    if not any(populated(x) for x in (desc,unit,amount)):continue
    work_rows+=1
    if work_rows>MAX_WORK_ROWS:raise ValueError('VOR source work row budget exceeds5000; no truncated result')
    trace=f'{file["sha256"][:16]}:f{fi}:s{si}:work:{row}';source={'file':file['name'],'source_hash':file['sha256'],'sheet':sheet['sheet'],'row':row,'section_id':section['id'] if section else None,'section_title':section['title'] if section else None,'cells':cells}
    def reject(reason):
     rejected.append(dict(source,id=trace,kind='WORK_REJECTED',reason=reason));row_registry.append(dict(source,id=trace,kind='WORK_REJECTED',reason=reason));gap(trace+': '+reason)
    if not populated(desc) or not isinstance(desc['value'],str):reject('Missing explicit work description');continue
    if not populated(unit):reject('Missing explicit physical unit');continue
    if not populated(amount):reject('Quantity missing/empty; unknown is not zero');continue
    value=amount.get('cached') if amount.get('data_type')=='f' else amount['value']
    if amount.get('data_type')=='f' and formula_status.get(amount['cell'])!='MATCH':reject('Quantity formula has no independent MATCH: '+amount['cell']);continue
    if value is None:reject('Quantity cache is unknown');continue
    try:physical_value,physical,factor=unit_value(unit['value'],value)
    except ValueError as e:reject(str(e));continue
    evidence={k:source[k] for k in ('file','source_hash','sheet','row','section_id','section_title')};evidence.update(description_cell=desc['cell'],unit_cell=unit['cell'],quantity_cell=amount['cell'],original_value=str(value),original_unit=unit['value'],explicit_source_unit_factor=factor)
    line={'id':trace,'description':desc['value'],'quantity':{'mode':'physical','value':physical_value,'physical_unit':physical,'evidence':evidence},'source_provenance':source}
    for key in ('technology','materials','technical_conditions'):
     if populated(selected.get(key)):line[key]=str(selected[key]['value'])
    if populated(selected.get('source_id')):line['original_source_id']=str(selected['source_id']['value'])
    # A workbook flag alone is never normative authority; require a scope declaration.
    declared=str(params.get('scope') or '')
    direct=params.get('base_direct_only') is True and (params.get('calculation_scope')=='BASE_DIRECT_ONLY' or bool(re.search(r'base\s+direct|базисн\w*\s+прям',declared,re.I)))
    if direct:
     for key in ('overhead','profit'):line[key]={'percent':'0','basis':'FOT','scope_trace_id':trace,'evidence':{'source_metadata':metadata,'scope':declared,'condition':'Explicit base direct cost scenario only; no statutory rate asserted'},'applicability_confirmed':False}
     gap('Scoped base direct scenario excludes NR/SP; normative applicability remains unconfirmed')
    lines.append(line);row_registry.append(dict(source,id=trace,kind='WORK_ACCEPTED'))
   if not header_seen and rows:gap(file['name']+'/'+sheet['sheet']+': No unambiguous VOR header; no quantities inferred')
 if not lines:gap('No valid source work rows; consult rejected_rows and source_row_registry')
 scope={'namespace':params.get('namespace'),'revision':params.get('revision'),'applicability_confirmed':False}
 if not scope['namespace'] or not scope['revision']:gap('Source normative namespace/revision absent; candidate retrieval is not applicability confirmation')
 return {'schema':'smetchik-new-vor-v1','scope':str(params.get('scope') or 'Base review draft; project and normative applicability require confirmation'),'price_basis':'BASE_NET','normative_scope':scope,'lines':lines,'source_sections':sections,'source_metadata':metadata,'source_parameters':params,'missing_data':missing,'rejected_rows':rejected,'source_row_registry':row_registry,'source_work_rows':work_rows,'approval':'NOT_FOR_APPROVAL'}


def declared_technical_gaps(row):
 """Only explicit absence declarations; no approval from textual similarity."""
 gaps=[]
 absent=r'не\s+(?:задан\w*|предоставлен\w*|указан\w*|определен\w*|определён\w*)|нужно\s+уточнить|требует\s+уточнения'
 parameters={'diameter':r'диаметр','method':r'способ|технолог','layers':r'сло[йяеёв]','orientation':r'ориентац','equipment_size':r'ковш|размер','soil_group':r'групп\w*\s+грунт'}
 for field in ('technology','materials','technical_conditions'):
  for clause in re.split(r'[;.\n]',str(row.get(field,''))):
   if not re.search(absent,clause,re.I):continue
   for parameter,pattern in parameters.items():
    if re.search(pattern,clause,re.I):gaps.append({'parameter':parameter,'source_field':field,'source_literal':clause.strip()})
 return gaps
