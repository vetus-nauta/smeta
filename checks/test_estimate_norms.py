"""Integration tests against the approved read-only source; no normative fixtures invented."""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import estimate_norms as norms
import estimate_storage as storage

class NormsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scopes=norms.catalog()['scopes']
        cls.scope=next(s for s in cls.scopes if s['namespace'].endswith('/ФЕР') and s['kind']=='norm')
    def test_cli_managed_output_refuses_tmp_and_home(self):
        for path in ('/tmp/estimate-result.json','/home/alexey/estimate-result.json','/data/estimate-result.json'):
            with self.assertRaises(RuntimeError):storage.managed_output(path,'test-output')
    def test_cli_output_validation_creates_nothing(self):
        path=Path('/ai-data/research/estimates/reference-07-01-01/not-created-by-validator.json')
        self.assertFalse(path.exists())
        self.assertEqual(storage.managed_output(path,'test-output'),path)
        self.assertFalse(path.exists())
        with self.assertRaises(RuntimeError):storage.register_output(path,'test-output')
    def test_cli_input_excluded_disk(self):
        with self.assertRaises(RuntimeError):storage.authorized_input('/data/estimate-input.json')
    def test_storage_symlink_rejected_before_connect(self):
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaises(RuntimeError):norms.catalog()
    def test_unregistered_staging_refused(self):
        with patch('estimate_norms.sqlite3.connect') as connect:
            connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value=[]
            with self.assertRaises(RuntimeError):norms.catalog()
            self.assertEqual(connect.call_count,1) # Only registry opened, normative source refused.
    def test_norm_calculation_preserves_uncounted_project_resources(self):
        r=norms.exact_lookup(self.scope['namespace'],self.scope['revision'],'27-06-002-03')['records'][0]
        result=norms.norm_to_calculation(r)
        self.assertEqual(result['physical_unit'],'m2');self.assertEqual(result['norm_factor'],'1000')
        self.assertEqual(result['unit'],r['unit']);self.assertIn(r['source_id'],result['source'])
        self.assertEqual(result['components']['labour'],'1512.80')
        self.assertEqual(result['components']['machines'],'6361.87')
        self.assertFalse(result['applicability_confirmed'])
        concrete=next(x for x in result['resources'] if x['code']=='04.1.02.03')
        reinforcement=next(x for x in result['resources'] if x['code']=='08.4.03.03')
        self.assertFalse(concrete['included']);self.assertIsNone(concrete['external_price'])
        self.assertIsNone(reinforcement['quantity_per_norm']);self.assertEqual(reinforcement['quantity_raw'],'П')
        self.assertEqual(result['status'],'NEEDS_SOURCE_DATA')
    def test_calculation_adapter_ignores_tampered_price_payload(self):
        r=norms.exact_lookup(self.scope['namespace'],self.scope['revision'],'27-06-002-03')['records'][0]
        r['prices']=[]
        self.assertEqual(norms.norm_to_calculation(r)['components']['labour'],'1512.80')
    def test_catalog_dynamic_sources(self):
        self.assertTrue(self.scopes)
        self.assertTrue(all(s['source_id'] and s['authority'] for s in self.scopes))
    def test_unknown_older_revision_no_substitution(self):
        r=norms.search_candidates('Устройство дорожного покрытия',source_metadata={'namespace':self.scope['namespace'],'amendments':[1,2,3]})
        self.assertEqual(r['status'],'NOT_AVAILABLE');self.assertEqual(r['candidates'],[])
    def test_wrong_revision_and_missing_code(self):
        self.assertEqual(norms.exact_lookup(self.scope['namespace'],'missing-revision','01-01-001-01')['status'],'NOT_AVAILABLE')
        self.assertEqual(norms.exact_lookup(self.scope['namespace'],self.scope['revision'],'NONEXISTENT')['status'],'NOT_AVAILABLE')
    def test_heldout_excavation_description(self):
        r=norms.search_candidates('Разработка грунта экскаватором',namespace=self.scope['namespace'],revision=self.scope['revision'],limit=4)
        self.assertTrue(r['candidates']);self.assertTrue(any('экскаватор' in x['name'].lower() for x in r['candidates']))
        self.assertTrue(all(x['namespace']==self.scope['namespace'] for x in r['candidates']))
    def test_bucket_volume_and_soil_group_rank_actual_names(self):
        r=norms.search_candidates('Разработка грунта экскаватором ковш 0,65 м3 группа грунтов 2',namespace=self.scope['namespace'],revision=self.scope['revision'],limit=10)
        top=r['candidates'][0]
        self.assertIn('0,65',top['name']);self.assertIn('группа грунтов 2',top['name'])
        numeric=top['rank_evidence']['numeric_conditions']
        self.assertEqual(len(numeric['matches']),2);self.assertFalse(numeric['mismatches'])
        self.assertFalse(top['rank_evidence']['technical_conditions_verified'])
    def test_numeric_mismatch_is_explicit(self):
        evidence=norms._numeric_evidence(norms._technical_numbers('ковш 0.65 m3 группа грунтов 2'),'Экскаватор ковш 8 м3, группа грунтов 1')
        self.assertEqual(len(evidence['mismatches']),2)
    def test_physical_units_do_not_hide_normative_multiplier(self):
        r=norms.search_candidates('Разработка грунта экскаватором',namespace=self.scope['namespace'],revision=self.scope['revision'],unit='m3',limit=3)
        self.assertTrue(all(x['rank_evidence']['physical_unit_compatible'] for x in r['candidates']))
        self.assertTrue(all(not x['rank_evidence']['exact_norm_unit_matches'] for x in r['candidates']))
        self.assertTrue(all(x['rank_evidence']['norm_unit']['norm_factor']=='1000' for x in r['candidates']))
        for unit in ('m2','m3','m','piece','t','kg'):
            self.assertIsNotNone(norms._unit_info(unit)['physical_unit'])
    def test_independent_source_gold_retrieval(self):
        # Gold codes independently chosen by reading source NAME/composition, not search output.
        cases=[
          ('Разработка грунта экскаватором ковш 0,65 м3 группа грунтов 2','погрузка автомобили самосвалы','','m3',{'01-01-013-08'}),
          ('Устройство кровли скатной из наплавляемых материалов','три слоя','','m2',{'12-01-001-03','12-01-001-04'}),
          ('Огрунтовка металлических поверхностей','один раз','ГФ-021','m2',{'13-03-002-04'})]
        retrieved=relevant=gold_total=0
        for description,technology,materials,unit,gold in cases:
            for code in gold:self.assertEqual(norms.exact_lookup(self.scope['namespace'],self.scope['revision'],code)['status'],'FOUND')
            r=norms.search_candidates(description,technology=technology,materials=materials,unit=unit,namespace=self.scope['namespace'],revision=self.scope['revision'],limit=10)
            codes=[x['code'] for x in r['candidates']]
            self.assertIn(codes[0],gold)
            hits=len(gold.intersection(codes));relevant+=hits;retrieved+=len(codes);gold_total+=len(gold)
            self.assertEqual(hits,len(gold));self.assertTrue(all(not x['rank_evidence']['technical_conditions_verified'] for x in r['candidates']))
        print('\nSource-gold retrieval: cases=3, precision@10=%.6f recall@10=%.6f (gold relevance only, no exhaustive expert relevance claim)'%(relevant/retrieved,relevant/gold_total))
    def test_heldout_roofing_description(self):
        r=norms.search_candidates('Устройство рулонной кровли',namespace=self.scope['namespace'],revision=self.scope['revision'],limit=4)
        self.assertTrue(any('кров' in x['name'].lower() for x in r['candidates']))
    def test_montage_namespace_collision(self):
        scopes=[s for s in self.scopes if s['namespace'].endswith('/ГЭСНм') and s['kind']=='norm']
        self.assertTrue(scopes);s=scopes[0]
        r=norms.search_candidates('Монтаж силового трансформатора',namespace=s['namespace'],revision=s['revision'],limit=3)
        self.assertTrue(r['candidates']);self.assertTrue(all(x['namespace']==s['namespace'] for x in r['candidates']))
        self.assertTrue(any('трансформатор' in x['name'].lower() for x in r['candidates']))
    def test_unit_mismatch_explicit(self):
        r=norms.search_candidates('Разработка грунта экскаватором',namespace=self.scope['namespace'],revision=self.scope['revision'],unit='шт',limit=3)
        self.assertTrue(r['candidates']);self.assertTrue(all(x['rank_evidence']['unit_matches'] is False for x in r['candidates']))
    def test_period_not_current_default(self):
        r=norms.search_candidates('Устройство покрытия',namespace=self.scope['namespace'],calculation_period='1900-01-01')
        self.assertEqual(r['status'],'NOT_AVAILABLE')
    def test_immutable_calculation_identity(self):
        r=norms.exact_lookup(self.scope['namespace'],self.scope['revision'],'01-01-001-01')['records'][0]
        identity=r['source_identity'];self.assertEqual(norms.calculation_record(identity)['content_hash'],r['content_hash'])
        with self.assertRaises(ValueError):norms.calculation_record({**identity,'content_hash':'tampered'})
        self.assertEqual(r['applicability'],'NOT_VERIFIED')
    def test_material_resource_search(self):
        r=norms.search_candidates('неизвестная операция',materials='песок',namespace=self.scope['namespace'],revision=self.scope['revision'],limit=3)
        self.assertTrue(r['candidates'])
        self.assertTrue(any(x['rank_evidence']['matched_prefix_tokens']['materials'] for x in r['candidates']))
        self.assertTrue(all(x['resources'] for x in r['candidates']))
    def test_conflicting_source_metadata(self):
        r=norms.search_candidates('Устройство покрытия',namespace=self.scope['namespace'],source_metadata={'namespace':'another-scope'})
        self.assertEqual(r['status'],'NOT_AVAILABLE')
    def test_technical_conditions_evidence_not_approval(self):
        r=norms.search_candidates('Устройство рулонной кровли',technology='наплавление',technical_conditions='три слоя',namespace=self.scope['namespace'],revision=self.scope['revision'],limit=2)
        self.assertTrue(r['candidates']);self.assertTrue(all(not x['rank_evidence']['technical_conditions_verified'] for x in r['candidates']))

if __name__=='__main__':unittest.main()
