#!/usr/bin/env python3
"""Explicit quote normalization; no statutory rates or source selection inferred."""
import argparse
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

def number(data, field, positive=False):
    if field not in data or isinstance(data[field], bool) or data[field] is None:
        raise ValueError('Missing/invalid field: '+field)
    try:
        value = Decimal(str(data[field]))
    except InvalidOperation:
        raise ValueError('Invalid decimal: '+field)
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError('Out of range: '+field)
    return value

def normalize(q):
    price = number(q, 'quote_price', positive=True)
    rate = number(q, 'vat_rate')
    if rate > 1:
        raise ValueError('vat_rate must be a fraction, e.g. 0.22')
    mode = q.get('vat_mode')
    if mode not in ('included', 'excluded', 'not_applicable'):
        raise ValueError('Explicit vat_mode is required')
    if mode == 'not_applicable' and rate != 0:
        raise ValueError('not_applicable requires vat_rate=0 and documented tax basis')
    units = number(q, 'base_units_per_quote_unit', positive=True)
    quantity = number(q, 'base_quantity', positive=True)
    exchange = number(q, 'rub_per_currency', positive=True)
    delivery = number(q, 'delivery_total_net_rub')
    storage_rate = number(q, 'zsr_rate')
    if storage_rate > 1:
        raise ValueError('zsr_rate must be a fraction')
    for field in ('delivery_included', 'zsr_included'):
        if type(q.get(field)) is not bool:
            raise ValueError('Explicit boolean required: '+field)
    if q['delivery_included'] and delivery != 0:
        raise ValueError('Delivery would be counted twice')
    if q['zsr_included'] and storage_rate != 0:
        raise ValueError('Storage costs would be counted twice')
    if not isinstance(q.get('basis'), str) or not q['basis'].strip():
        raise ValueError('Documented conversion/cost basis is required')
    net = price / (1+rate) if mode == 'included' else price
    unit = net * exchange / units
    transport = delivery / quantity
    subtotal = unit + transport
    zsr = subtotal * storage_rate
    total = subtotal + zsr
    return {'id': q.get('id'), 'net_unit_rub': str(unit),
            'delivery_unit_net_rub': str(transport), 'zsr_unit_rub': str(zsr),
            'normalized_unit_net_rub': str(total),
            'display_unit_net_rub': str(total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
            'basis': q['basis'],
            'limitation': 'Arithmetic only; comparability, tax applicability and statutory compliance not validated'}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('file', type=Path)
    a = ap.parse_args()
    data = json.loads(a.file.read_text())
    print(json.dumps([normalize(q) for q in data['quotes']], ensure_ascii=False, indent=2))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as exc:
        raise SystemExit(str(exc))
