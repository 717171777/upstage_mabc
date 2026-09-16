"""Conservative exact-whitespace mapping of a wrapped PDF address.

Only complete text lines in a single unambiguous, nearby aligned chain qualify.
The returned spans always address the original per-line character arrays.
"""
import hashlib
import json
import math
import re


def _box(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or not all(type(v) in (int, float) and math.isfinite(v) for v in value)
            or value[2] <= value[0] or value[3] <= value[1]):
        return None
    return value


def _line(unit):
    text = unit.get('text')
    chars = unit.get('chars')
    box = _box(unit.get('locator', {}).get('bbox'))
    page = unit.get('page')
    if (not isinstance(text, str) or not text.strip() or not box
            or type(page) is not int or page < 1
            or unit.get('locator', {}).get('page') != page
            or not isinstance(chars, list) or len(chars) != len(text)):
        return None
    for char, expected in zip(chars, text):
        if not isinstance(char, dict) or char.get('c') != expected:
            return None
        cb = _box(char.get('bbox'))
        if not cb or not (box[0] - .01 <= cb[0] < cb[2] <= box[2] + .01
                          and box[1] - .01 <= cb[1] < cb[3] <= box[3] + .01):
            return None
    start = len(text) - len(text.lstrip())
    end = len(text.rstrip())
    return {'unitId': unit['id'], 'page': page, 'box': box,
            'start': start, 'end': end, 'value': text[start:end],
            'normalized': re.sub(r'\s+', '', text)}


def locate_multiline_address(inspection: dict, value: str) -> list[dict]:
    """Return 2–3 exact original spans; ambiguity/malformed geometry returns []."""
    if inspection.get('format') != 'pdf' or not isinstance(value, str) or not value.strip():
        return []
    target = re.sub(r'\s+', '', value)
    lines = []
    invalid_pages = set()
    for unit in inspection.get('units', []):
        line = _line(unit)
        if line:
            lines.append(line)
        elif isinstance(unit.get('text'), str) and unit['text'].strip():
            invalid_pages.add(unit.get('page'))
    # Never skip an unreadable intervening line to manufacture adjacency.
    lines = [line for line in lines if line['page'] not in invalid_pages]
    by_page = {}
    for line in lines:
        by_page.setdefault(line['page'], []).append(line)
    matches = []
    for first in lines:
        if not first['normalized'] or not target.startswith(first['normalized']):
            continue
        chain = [first]
        normalized = first['normalized']
        for _ in range(2):
            previous = chain[-1]
            x0, y0, x1, y1 = previous['box']
            height = y1 - y0
            next_lines = []
            for line in by_page[first['page']]:
                bx0, by0, bx1, by1 = line['box']
                tolerance = max(2, min(height, by1 - by0) * .4)
                if (line['unitId'] not in {part['unitId'] for part in chain}
                        and abs(bx0 - first['box'][0]) <= tolerance
                        and -.01 <= by0 - y1 <= min(24, height * 1.6)):
                    next_lines.append(line)
            next_lines.sort(key=lambda line: line['box'][1])
            if not next_lines:
                break
            # Two overlapping next rows are ambiguous; do not choose by content.
            if len(next_lines) > 1 and abs(next_lines[0]['box'][1] - next_lines[1]['box'][1]) <= 1:
                break
            following = next_lines[0]
            normalized += following['normalized']
            chain.append(following)
            if normalized == target:
                matches.append(chain.copy())
                break
            if not target.startswith(normalized):
                break
    # Repeated or competing chains require a person to locate the intended text.
    if len(matches) != 1:
        return []
    chain = matches[0]
    identity = [(part['unitId'], part['start'], part['end']) for part in chain]
    group = 'address_' + hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()[:24]
    return [{k: part[k] for k in ('unitId', 'start', 'end', 'page', 'value')} | {
        'linkedValue': value, 'linkGroup': group, 'linkIndex': index,
        'linkCount': len(chain),
    } for index, part in enumerate(chain, 1)]
