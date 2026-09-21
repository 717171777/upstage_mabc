"""Conservative PDF column evidence using literal character coordinates."""
import re


def _rows(units):
    rows = []
    for unit in sorted(units, key=lambda u: (u['locator']['bbox'][1], u['locator']['bbox'][0])):
        y = unit['locator']['bbox'][1]
        if not rows or abs(y - rows[-1][0]['locator']['bbox'][1]) > 3:
            rows.append([])
        rows[-1].append(unit)
    return rows


def table_fields(units, label_type, valid_value):
    """Yield source ranges only within contiguous, consistently aligned tables.

    Three recognised headers and at least two validated data columns are required.
    A numbering column, when present, must contain a number. A gap, heading,
    invalid row or another page ends the table; no nearest-heading propagation.
    """
    pages = {}
    for unit in units:
        box = unit.get('locator', {}).get('bbox')
        if isinstance(box, (list, tuple)) and len(box) == 4:
            pages.setdefault(unit.get('page'), []).append(unit)
    contacts = {'연락처': 'phone', '전화번호': 'phone', '휴대전화': 'phone',
                '이메일': 'email', '전자우편': 'email', 'no': 'number', '번호': 'number', '순번': 'number'}
    for page_units in pages.values():
        headers = []
        previous_bottom = 0
        for row in _rows(page_units):
            possible = []
            for unit in sorted(row, key=lambda u: u['locator']['bbox'][0]):
                kind = label_type(unit['text']) or contacts.get(re.sub(r'\s+', '', unit['text']).lower())
                if kind:
                    possible.append((unit, kind))
            if len(possible) >= 3 and len(possible) == len(row) and len({kind for _, kind in possible}) == len(possible):
                headers = possible
                previous_bottom = max(u['locator']['bbox'][3] for u in row)
                continue
            if not headers:
                continue
            height = max(u['locator']['bbox'][3] - u['locator']['bbox'][1] for u, _ in headers)
            if row[0]['locator']['bbox'][1] - previous_bottom > max(24, height * 3):
                headers = []
                continue
            fields = []
            valid_row = True
            for index, (header, kind) in enumerate(headers):
                left = header['locator']['bbox'][0] - 2
                right = headers[index + 1][0]['locator']['bbox'][0] - 2 if index + 1 < len(headers) else float('inf')
                cells = []
                for unit in row:
                    chars = unit.get('chars', [])
                    if len(chars) == len(unit['text']) and chars:
                        indices = [i for i, ch in enumerate(chars) if not ch['c'].isspace()
                                   and left <= (ch['bbox'][0] + ch['bbox'][2]) / 2 < right]
                        if indices:
                            cells.append((unit, indices[0], indices[-1] + 1))
                    elif left <= unit['locator']['bbox'][0] and unit['locator']['bbox'][2] <= right:
                        text = unit['text']
                        cells.append((unit, len(text) - len(text.lstrip()), len(text.rstrip())))
                # Multiple fragments in one cell need a dedicated reconciliation,
                # never concatenate them and invent a source range.
                if len(cells) != 1:
                    valid_row = False
                    break
                unit, start, end = cells[0]
                value = unit['text'][start:end]
                if kind == 'number':
                    valid = bool(re.fullmatch(r'\d{1,6}', value))
                elif kind == 'phone':
                    valid = bool(re.fullmatch(r'0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}', value))
                elif kind == 'email':
                    valid = bool(re.fullmatch(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', value))
                else:
                    valid = valid_value(kind, value)
                if not valid:
                    valid_row = False
                    break
                if kind not in ('number', 'phone', 'email'):
                    fields.append((unit, kind, start, end, header))
            if valid_row:
                yield from fields
                previous_bottom = max(u['locator']['bbox'][3] for u in row)
            else:
                headers = []
