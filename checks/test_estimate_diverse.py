"""Counterexample regression, not substitute-model E2E acceptance."""
import sys,unittest,json,uuid
from pathlib import Path
sys.path[:0]=['/ai/estimates-agent',str(Path(__file__).resolve().parents[1]/'tools')]
import estimate_norms as norms
import estimate_calculation as calc
import estimate_vor as vor
from agent.estimate_workflow import action_schema
from agent.storage import station
from openpyxl import load_workbook
class DiverseCounterexamples(unittest.TestCase):
 def test_interval_bucket_and_upper_bound_are_not_equality(self):
  scope=next(s for s in norms.catalog()['scopes'] if s['namespace'].endswith('/ФЕР') and s['kind']=='norm')
  r=norms.exact_lookup(scope['namespace'],scope['revision'],'01-01-013-08')['records'][0]
  for value in ('0.5','0.9','1'):
   e=norms._numeric_evidence(norms._technical_numbers('ковш '+value+' м3'),r['name']);self.assertEqual(len(e['matches']),1);self.assertFalse(e['mismatches'])
  self.assertTrue(norms._numeric_evidence(norms._technical_numbers('ковш 1.8 м3'),r['name'])['mismatches'])
  self.assertTrue(norms._numeric_evidence(norms._technical_numbers('фундамент 2 м3'),'Фундаменты объемом до 3 м3')['matches'])
 def test_layer_count_does_not_hide_method(self):
  match=norms._numeric_evidence(norms._technical_numbers('изоляция в два слоя'),'изоляция в 2 слоя');self.assertTrue(match['matches'])
  mismatch=norms._numeric_evidence(norms._technical_numbers('изоляция в три слоя'),'изоляция в 2 слоя');self.assertTrue(mismatch['mismatches'])
 def test_word_prefix_does_not_match_unrelated_substrings(self):
  self.assertEqual(norms._prefix_hits(['пол'],'полы получены'),['пол'])
  self.assertEqual(norms._prefix_hits(['пол'],'получены'),['пол']) # A prefix is explicitly retrieval, not semantic equivalence.
  self.assertEqual(norms._prefix_hits(['пол'],'тополь'),[])
 def test_missing_diameter_is_data_gap_price_is_not_technology_gap(self):
  row={'technical_conditions':'Диаметр не предоставлен; Цена не предоставлена'}
  self.assertEqual([g['parameter'] for g in vor.declared_technical_gaps(row)],['diameter'])
  self.assertEqual(vor.declared_technical_gaps({'technical_conditions':'Диаметр 20 мм; Цена не предоставлена'}),[])
  self.assertTrue(vor.declared_technical_gaps({'technology':'Способ и число слоев не заданы'}))
 def test_brick_half_and_height_range_are_distinct_from_whole(self):
  requested=norms._technical_numbers('В половину кирпича; высота этажа 3 м')
  half=norms._numeric_evidence(requested,'Облицовка в 1/2 кирпича при высоте этажа до 4 м')
  self.assertFalse(half['mismatches']);self.assertEqual(len(half['matches']),2)
  whole=norms._numeric_evidence(requested,'Облицовка в 1 кирпич при высоте этажа до 4 м')
  self.assertTrue(any(x['dimension']=='brick_thickness' for x in whole['mismatches']))
 def test_read_schema_and_selection_have_distinct_evidence_ids(self):
  s=action_schema(['read_norm','select_norm'],{'line_ids':['L1'],'candidate_ids':[12,13],'read_candidate_ids':[11]},composition=True)
  b={x['properties']['tool']['enum'][0]:x['properties']['candidate_id']['enum'] for x in s['oneOf']}
  self.assertEqual(b['read_norm'],[12,13]);self.assertEqual(b['select_norm'],[11,None]);self.assertNotIn(None,b['read_norm'])
 def test_export_preserves_refusal_unknown_and_literal_text(self):
  root=station().resolve_storage('estimates','research',write=True)/'diverse-vor-acceptance-20261008'/'test-artifacts';root.mkdir(exist_ok=True)
  station().register(dict(data_id='diverse-counterexample-fixtures',name='Synthetic export regression',project='estimates',data_class='research',canonical_path=str(root),owner_component='test-suite'))
  p=root/(uuid.uuid4().hex+'.xlsx')
  rows=[dict(trace_id='L1',description='=HYPERLINK("x")',source_locator={'sheet':'Source','row':4},physical_quantity='1.27',physical_unit='t',selected_code=None,status='NEEDS_CLARIFICATION',choice_reason='Missing construction technology',net=None),dict(trace_id='L2',description='Missing quantity',source_locator={'sheet':'Source','row':5},physical_quantity=None,physical_unit=None,selected_code=None,status='MISSING_INPUT',choice_reason='Quantity not supplied',net=None)]
  calc.export_xlsx(dict(status='BASE_ONLY_DRAFT',lines=[],source_rows=rows,totals={'net':None},missing_data=[]),p)
  book=load_workbook(p,data_only=False);s=book['Все исходные позиции'];self.assertEqual(s.max_row,4);self.assertEqual(s['B3'].data_type,'s');self.assertEqual(s['F3'].data_type,'n');self.assertEqual(s['F4'].value,'НЕИЗВЕСТНО');self.assertEqual(s['K3'].value,'НЕИЗВЕСТНО');book.close()
if __name__=='__main__':unittest.main()
