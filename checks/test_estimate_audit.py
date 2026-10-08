"""Adversarial semantic and arithmetic checks with in-memory source fixtures."""
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
import zipfile

S = importlib.util.spec_from_file_location('estimate_audit', Path(__file__).parents[1] / 'tools/estimate_audit.py')
M = importlib.util.module_from_spec(S); S.loader.exec_module(M)


def archive(name, *, symlink=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        entry = zipfile.ZipInfo(name); entry.create_system = 3
        if symlink: entry.external_attr = (0o120777 << 16)
        z.writestr(entry, b'target')
    return stream.getvalue()


def supplier_fixture(text, method='TEXT_LAYER'):
    return {'files': [{'name': 'analysis.gsfx', 'format': '.gsfx', 'members': [
        {'name': 'Data.xml', 'format': '.xml', 'xml': {
            'formulas': [], 'positions': [], 'nodes': [
                {'tag': 'Counterparty', 'path': '/Party', 'attributes': {'Caption': 'ООО "ТестовыйПоставщик"', 'INN': '1111111111'}},
                {'tag': 'Item', 'path': '/Offer', 'attributes': {'Supplier': '1'}},
                {'tag': 'Item', 'path': '/Offer/Hyperlinks/Item', 'attributes': {'Caption': 'offer.pdf'}}]}},
        {'name': 'offer.pdf', 'format': '.pdf', 'pages': [{'page': 1, 'method': method, 'text': text}]}]}]}


class AuditTests(unittest.TestCase):
    def test_normative_measure_applied_once(self):
        raw = b'<Document><Position SysID="1" Quantity="274/100"><Quantity Fx="274" KUnit="100" Result="2.74"/></Position></Document>'
        formulas = M.xml_read(raw)['formulas']
        quantity = next(f for f in formulas if f['field'] == 'Fx')
        self.assertEqual(quantity['status'], 'MATCH')
        self.assertEqual(quantity['calculated'], '2.74')
        self.assertFalse(any(f['status'] == 'MISMATCH' for f in formulas))

    def test_resource_reference_uses_norm_units(self):
        raw = '<Document><Position Identifier="Ф1" SysID="1"><Quantity Fx="274" KUnit="100" Result="2.74"/><Resources><Mat Identifier="р1" Quantity="7"/></Resources></Position><Position SysID="2"><Quantity Fx="-Ф1.р1" Result="-19.18"/></Position></Document>'.encode()
        formula = next(f for f in M.xml_read(raw)['formulas'] if f['expression'] == '-Ф1.р1')
        self.assertEqual(formula['status'], 'MATCH')

    def test_resource_reference_material_quantity_coefficient_and_sign(self):
        for sign in ('', '-'):
            expected = sign + '327.3792'
            expression = sign + '(Ф10.р1+Ф11.р1)'
            raw = ('<Document>'
                   '<Position Identifier="Ф10" SysID="10"><Quantity Result="37.76"/><Resources><Mat Identifier="р1" Quantity="2.04"/></Resources></Position>'
                   '<Position Identifier="Ф11" SysID="11"><Quantity Result="37.76"/><Resources><Mat Identifier="р1" Quantity="0.51"/></Resources>'
                   '<Koefficients><K Options="MatQty" Value_PZ="13"/></Koefficients></Position>'
                   '<Position SysID="12"><Quantity Fx="' + expression + '" Result="' + expected + '"/></Position>'
                   '</Document>').encode()
            formula = next(f for f in M.xml_read(raw)['formulas'] if f['expression'] == expression)
            with self.subTest(sign=sign):
                self.assertEqual(formula['status'], 'MATCH')
                self.assertEqual(formula['calculated'], expected)

    def test_price_only_coefficient_does_not_change_resource_consumption(self):
        raw = ('<Document>'
               '<Position Identifier="Ф10" SysID="10"><Quantity Result="37.76"/><Resources><Mat Identifier="р1" Quantity="2.04"/></Resources></Position>'
               '<Position Identifier="Ф11" SysID="11"><Quantity Result="37.76"/><Resources><Mat Identifier="р1" Quantity="0.51"/></Resources>'
               '<Koefficients><K Options="Mat" Value_PZ="13"/><K Options="Ozp" Value_PZ="2"/></Koefficients></Position>'
               '<Position SysID="12"><Quantity Fx="Ф10.р1+Ф11.р1" Result="96.288"/></Position>'
               '</Document>').encode()
        formula = next(f for f in M.xml_read(raw)['formulas'] if f['expression'] == 'Ф10.р1+Ф11.р1')
        self.assertEqual(formula['status'], 'MATCH')
        self.assertEqual(formula['calculated'], '96.2880')

    def test_missing_cache_is_not_numeric_zero(self):
        from openpyxl import Workbook
        stream = io.BytesIO(); book = Workbook(); book.active['A1'] = '=2+3'; book.save(stream); book.close()
        record = M.spreadsheet_read(stream.getvalue())[0]['formulas'][0]
        self.assertIsNone(record['cached'])
        self.assertEqual(record['status'], 'NO_CACHE')
        self.assertEqual(record['calculated'], '5')

    def test_buyer_inn_not_supplier_inn(self):
        texts = ['Коммерческое предложение\nЗаказчик ООО ТестовыйПокупатель ИНН 2222222222',
                 'ООО ТестовыйПоставщик предлагает товар.\nЗаказчик Заказчик ООО ТестовыйПокупатель ИНН 2222222222']
        for text in texts:
            with self.subTest(text=text):
                findings = M.controls(supplier_fixture(text))['findings']
                self.assertFalse(any(f['kind'] == 'SUPPLIER_INN' for f in findings))

    def test_seller_inn_discrepancy_and_ocr_uncertainty(self):
        text = 'Общество с ограниченной ответственностью «ТестовыйПоставщик»\nИНН/КПП 3333333333/444444444\nКоммерческое предложение\nЗаказчик ООО ТестовыйПокупатель ИНН 2222222222'
        records = M.controls(supplier_fixture(text))['findings']
        f = next(x for x in records if x['kind'] == 'SUPPLIER_INN')
        self.assertEqual(f['classification'], 'CONFIRMED_ERROR')
        self.assertEqual(f['evidence']['pdf_inn'], '3333333333')
        records = M.controls(supplier_fixture(text, 'OCR_UNVERIFIED'))['findings']
        self.assertEqual(next(x for x in records if x['kind'] == 'SUPPLIER_INN')['classification'], 'POTENTIAL_ERROR')

    def test_unlabelled_dimensions_do_not_define_length(self):
        caption = 'Лоток водоотводный бетонный ЛД 500х200х60 мм'
        xml = {'formulas': [], 'positions': [{'path': '/Document/Position', 'active': True, 'attributes': {'Caption': caption, 'Number': '1', 'Units': 'шт'}}],
               'nodes': [{'path': '/Document/Position/Quantity', 'tag': 'Quantity', 'attributes': {'Result': '20'}}]}
        data = {'files': [
            {'name': 'ВОР.xlsx', 'format': '.xlsx', 'sheets': [{'sheet': 'ВОР', 'formulas': [], 'cells': [{'cell': 'C1', 'value': caption}, {'cell': 'D1', 'value': 'м.п.'}, {'cell': 'E1', 'value': 40}]}]},
            {'name': 'ДОП.gsfx', 'format': '.gsfx', 'members': [{'name': 'Data.xml', 'format': '.xml', 'xml': xml}]}]}
        findings = M.controls(data)['findings']
        for f in findings:
            if f['kind'] == 'LINEAR_PIECE_CONVERSION':
                self.assertEqual(f['classification'], 'INSUFFICIENT_EVIDENCE')
                self.assertNotIn('nominal_first_dimension_m', f['evidence'])
                self.assertNotIn('nominal_scenario_pieces', f['evidence'])
                self.assertNotEqual(f['evidence'].get('piece_length_m'), '0.5')

    def test_unsafe_archive_names_and_symlink(self):
        for name in ('../outside.xml', '/absolute.xml', 'C:/windows.xml', '..\\outside.xml'):
            with self.subTest(name=name), self.assertRaises(ValueError): list(M.zip_members(archive(name)))
        with self.assertRaises(ValueError): list(M.zip_members(archive('link.xml', symlink=True)))
        self.assertEqual(list(M.zip_members(archive('safe/file.xml')))[0][0], 'safe/file.xml')

    def test_input_directory_symlink_rejected(self):
        item = MagicMock(); item.is_symlink.return_value = True
        root = MagicMock(); root.rglob.return_value = [item]
        with patch.object(M, 'Path', return_value=root), self.assertRaises(ValueError): M.inspect_directory('mock-input')

    def test_formula_code_and_zero_measure_fail_closed(self):
        for expression in ('__import__("os").system("x")', '1/0', 'float("nan")', '2**10000'):
            with self.subTest(expression=expression), self.assertRaises(ValueError): M.arithmetic(expression)
        formulas = M.xml_read(b'<Document><Quantity Fx="4" KUnit="0" Result="0"/></Document>')['formulas']
        self.assertEqual(formulas[0]['status'], 'UNRESOLVED')


if __name__ == '__main__': unittest.main()
