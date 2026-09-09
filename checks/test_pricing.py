import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pricing', ROOT/'tools/pricing.py')
pricing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pricing)

class PriceChecks(unittest.TestCase):
    def setUp(self):
        self.quotes = json.loads((ROOT/'examples/pricing-input.json').read_text())['quotes']

    def test_explicit_comparable_arithmetic(self):
        self.assertEqual(pricing.normalize(self.quotes[0])['normalized_unit_net_rub'], '104.04')
        self.assertEqual(pricing.normalize(self.quotes[1])['normalized_unit_net_rub'], '105.06')

    def test_unknown_delivery_never_becomes_zero(self):
        q = copy.deepcopy(self.quotes[0]); q['delivery_total_net_rub'] = None
        with self.assertRaises(ValueError): pricing.normalize(q)
        del q['delivery_total_net_rub']
        with self.assertRaises(ValueError): pricing.normalize(q)

    def test_no_double_counting(self):
        for changes in ({'delivery_included':True}, {'zsr_included':True}):
            q = dict(self.quotes[0], **changes)
            with self.assertRaises(ValueError): pricing.normalize(q)

    def test_nonpayer_price_not_divided_by_vat(self):
        q = dict(self.quotes[1], vat_mode='not_applicable', vat_rate='0', zsr_rate='0')
        self.assertEqual(pricing.normalize(q)['normalized_unit_net_rub'], '103')

    def test_invalid_numbers_and_basis(self):
        for changes in ({'quote_price':'NaN'}, {'quote_price':'Infinity'}, {'quote_price':True},
                        {'base_quantity':'0'}, {'base_units_per_quote_unit':'0'},
                        {'delivery_total_net_rub':'-1'}, {'vat_rate':'22'},
                        {'basis':' '}, {'basis':{'fake':'basis'}}, {'delivery_included':'false'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError): pricing.normalize(dict(self.quotes[0], **changes))

if __name__ == '__main__': unittest.main()
