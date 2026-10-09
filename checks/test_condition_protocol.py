"""Provenance checks do not establish professional normative applicability."""
import sys,unittest
sys.path.insert(0,'/ai/estimates-agent')
from agent.estimate_composer import validate_condition_comparison
from agent.estimate_workflow import action_schema
class ConditionProtocolTests(unittest.TestCase):
 def setUp(self):
  self.refs=[{'id':'11:C0','literal':'Chapter construction'},{'id':'11:C1','literal':'Install specific work'}]
  self.row={'description':'Install work by specified method'}
  self.good={'11:C0':{'assessment':'CONTEXT_ONLY','source_field':None,'source_excerpt':'','explanation':'Broad construction chapter, no independent project parameter'},'11:C1':{'assessment':'SUPPORTED','source_field':'description','source_excerpt':'specified method','explanation':'Model compares actual installation method; professional review remains pending'}}
 def test_selected_candidate_only_schema(self):
  s=action_schema(['select_norm'],{'line_ids':['L'],'candidate_ids':[],'read_candidate_ids':[11,12],'candidate_condition_references':{11:['11:C0','11:C1'],12:['12:C0']}},composition=True)
  b=s['oneOf'];self.assertEqual(b[0]['properties']['conditions']['required'],['11:C0','11:C1']);self.assertEqual(b[1]['properties']['conditions']['required'],['12:C0']);self.assertEqual(b[2]['properties']['conditions']['required'],[])
 def test_schema_cites_actual_source_values_only(self):
  import jsonschema
  s=action_schema(['select_norm'],{'line_ids':['L'],'candidate_ids':[],'read_candidate_ids':[11],'candidate_condition_references':{11:['11:C0','11:C1']},'source_evidence_fields':{'description':'Actual source method'}},composition=True)
  props=s['oneOf'][0]['properties']['conditions']['properties']
  q=props['11:C0'];self.assertEqual(q['oneOf'][0]['properties']['source_excerpt']['enum'],['Actual source method'])
  context={'assessment':'CONTEXT_ONLY','source_field':None,'source_excerpt':'','explanation':'Broad chapter context only'}
  jsonschema.validate(context,q)
  with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(context,source_field='description',source_excerpt='Actual source method'),q)
  with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(context,props['11:C1'])
  supported=dict(context,assessment='DESCRIPTIVE_MATCH',source_field='description',source_excerpt='Actual source method')
  jsonschema.validate(supported,props['11:C1'])
  with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(supported,source_excerpt='Invented source'),props['11:C1'])
 def test_context_and_original_quote_are_proposals(self):self.assertIsNone(validate_condition_comparison(self.row,self.refs,self.good))
 def test_invented_source_excerpt_rejected(self):
  self.good['11:C1']['source_excerpt']='sliding formwork';self.assertIsNotNone(validate_condition_comparison(self.row,self.refs,self.good))
 def test_unsupported_requirement_rejected(self):
  self.good['11:C1']['assessment']='UNSUPPORTED';self.assertIsNotNone(validate_condition_comparison(self.row,self.refs,self.good))
 def test_title_cannot_be_dismissed_as_context(self):
  self.good['11:C1']=dict(self.good['11:C0']);self.assertIsNotNone(validate_condition_comparison(self.row,self.refs,self.good))
 def test_other_candidate_references_rejected(self):
  self.good['12:C0']=dict(self.good['11:C0']);self.assertIsNotNone(validate_condition_comparison(self.row,self.refs,self.good))
 def test_missing_comparison_rejected(self):self.assertIsNotNone(validate_condition_comparison(self.row,self.refs,{}))
class NegativeNormRequirementsTests(unittest.TestCase):
 def setUp(self):
  import importlib.util
  from pathlib import Path
  spec=importlib.util.spec_from_file_location('negative_norms',Path(__file__).resolve().parents[1]/'tools/estimate_norms.py');self.n=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.n)
 def test_unseen_specialized_scope_requires_original_source(self):
  for heading,source in [('ПОДВОДНЫЕ СВАРОЧНЫЕ РАБОТЫ','Подводная сварка'),('РАБОТЫ В МЕТРОПОЛИТЕНАХ','Монтаж в метрополитене'),('КОНСТРУКЦИИ АТОМНЫХ ЭЛЕКТРОСТАНЦИЙ','Конструкция атомной электростанции')]:
   record={'body':heading+' Монтаж элементов.','name':'Монтаж элементов'}
   self.assertTrue(self.n.selection_conflicts({'description':'Монтаж элементов','applicability_confirmed':True,'conditions':{heading:True}},record))
   self.assertFalse(self.n.selection_conflicts({'description':source},record)) # No conflict is NOT applicability approval.
 def test_water_management_is_not_generic_excavation(self):
  record={'name':'Устройство каналов и дамб','body':'Устройство каналов, дамб обвалования. Разработка грунта в отвал. Водохозяйственное строительство.'}
  self.assertTrue(self.n.selection_conflicts({'description':'Выемка котлована с погрузкой грунта'},record))
  self.assertFalse(self.n.selection_conflicts({'description':'Устройство каналов, дамб обвалования'},record))
 def test_opposed_orientations_cannot_be_substituted(self):
  for source,norm in [('Вертикальная оклеечная изоляция','Горизонтальная оклеечная изоляция'),('Горизонтальная изоляция','Вертикальная изоляция')]:
   self.assertTrue(self.n.selection_conflicts({'description':source},{'name':norm,'body':norm}))
   self.assertFalse(self.n.selection_conflicts({'description':source},{'name':source,'body':source}))
 def test_roll_material_is_not_applied_acrylic_coating(self):
  r={'name':'Изоляция эластичным покрытием на акриловой основе','body':'Нанесение покрытия на акриловой основе.'}
  self.assertTrue(self.n.selection_conflicts({'description':'Рулонная изоляция'},r))
  self.assertFalse(self.n.selection_conflicts({'description':'Изоляция покрытием на акриловой основе'},r))
 def test_main_roll_installation_is_distinct_from_auxiliary_mastic(self):
  source={'description':'Наплавляемый рулонный ковер','technology':'Наплавление; защитный слой на мастике'}
  self.assertTrue(self.n.selection_conflicts(source,{'body':'Наклейка рулонных материалов на битумной мастике. Устройство защитного слоя.','name':'Рулонная кровля'}))
  self.assertFalse(self.n.selection_conflicts(source,{'body':'Наклейка рулонных материалов методом подплавления мастичного слоя газопламенными горелками.','name':'Рулонная кровля'}))
 def test_adhesive_work_is_not_dry_laying(self):
  source={'description':'Оклеечная изоляция резервуара','technology':'Наклейка рулонного материала'}
  self.assertTrue(self.n.selection_conflicts(source,{'name':'Укладка рулонного материала насухо','body':'Разметка и сухая укладка.'}))
  self.assertFalse(self.n.selection_conflicts(source,{'name':'Наклейка рулонного материала','body':'Наклейка рулонного материала на мастике.'}))
 def test_norm_height_is_not_billed_work_quantity(self):
  record={'name':'Монтаж конструкции при высоте здания до 40 м','body':'Монтаж конструкции.'}
  self.assertTrue(self.n.selection_conflicts({'description':'Монтаж конструкции','quantity':{'value':'20','physical_unit':'m'}},record))
  self.assertFalse(self.n.selection_conflicts({'description':'Монтаж конструкции','technical_conditions':'Высота здания 20 м'},record))
 def test_named_protective_construction_is_not_generic_insulation(self):
  record={'name':'Изоляция с защитной мембраной','body':'Монтаж изоляции с защитной мембраной.'}
  self.assertTrue(self.n.selection_conflicts({'description':'Рулонная изоляция'},record))
  self.assertFalse(self.n.selection_conflicts({'description':'Рулонная изоляция с защитной мембраной'},record))
 def test_component_fraction_preserves_dimension(self):
  evidence=self.n._numeric_evidence({'brick_thickness':{'1.5'}},'Кладка в 1.5 кирпича')['matches'][0]
  self.assertEqual(evidence['dimension_unit'],'brick_count')
 def test_status_conclusion_cannot_invent_broken_input(self):
  import jsonschema
  from agent.estimate_workflow import action_schema
  s=action_schema(['report'],{'report_conclusion':'Observed source rows: 7; NOT_FOR_APPROVAL'},composition=True)
  jsonschema.validate({'tool':'report','reason':'Final observed status','conclusion':'Observed source rows: 7; NOT_FOR_APPROVAL'},s)
  with self.assertRaises(jsonschema.ValidationError):jsonschema.validate({'tool':'report','reason':'False assertion','conclusion':'Malformed input; no calculation possible'},s)
 def test_execution_resources_are_not_source_prerequisites(self):
  from agent.estimate_composer import resource_comparison_view
  resources=[{'code':'91.01.01-001','tag':'Resource','quantity_raw':'2','attributes_json':'{"EndName":"Compressor"}'},{'code':'01.2.01-001','tag':'AbstractResource','quantity_raw':'P','attributes_json':'{"Name":"Project sand"}'}]
  view=resource_comparison_view(resources)
  self.assertEqual(view['execution_equipment'][0]['name'],'Compressor')
  self.assertEqual(view['materials_and_other_resources'][0]['tag'],'AbstractResource')
  self.assertEqual(resources[0]['quantity_raw'],'2')
 def test_named_roof_scope_is_not_generic_foundation(self):
  record={'name':'Рулонная изоляция в 2 слоя','body':'Рулонная изоляция в 2 слоя Кровли КРОВЛИ Очистка основания и наклейка материала.'}
  self.assertTrue(self.n.selection_conflicts({'description':'Оклеечная защита фундамента','technical_conditions':'В 2 слоя'},record))
  self.assertFalse(self.n.selection_conflicts({'description':'Оклеечная кровля','technical_conditions':'В 2 слоя'},record))
 def test_failed_first_candidate_does_not_hide_an_alternative(self):
  from agent.estimate_workflow import action_schema
  state={'line_ids':['L'],'candidate_ids':[22],'read_candidate_ids':[21],'candidate_condition_references':{21:['21:C0']},'allow_null_selection':False}
  schema=action_schema(['read_norm','select_norm'],state,composition=True)
  selections=[b['properties']['candidate_id']['enum'] for b in schema['oneOf'] if b['properties']['tool']['enum']==['select_norm']]
  self.assertEqual(selections,[[21]])
  self.assertTrue(any(b['properties']['tool']['enum']==['read_norm'] for b in schema['oneOf']))
 def test_body_cannot_be_dismissed_as_context(self):
  from agent.estimate_composer import validate_condition_comparison
  refs=[{'id':'BODY','literal':'Main installation. Protective construction.','kind':'WORK_BODY'},{'id':'TITLE','literal':'Installation','kind':'TITLE'}]
  conditions={'BODY':{'assessment':'CONTEXT_ONLY','source_field':None,'source_excerpt':'','explanation':'Ignored as general context'},'TITLE':{'assessment':'SUPPORTED','source_field':'description','source_excerpt':'Installation','explanation':'Candidate proposal'}}
  self.assertIsNotNone(validate_condition_comparison({'description':'Installation'},refs,conditions))

if __name__=='__main__':unittest.main()
