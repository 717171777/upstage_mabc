"""Attach bounded, literal location facts to each resolved PII occurrence.

This module performs no privacy classification and makes no keep/mask decision.
Table first-row/first-cell and PDF neighbours describe physical relationships,
not a claim that their text is a semantic heading or applies to the candidate.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from pathlib import Path

from .document_layout import build_layout

_MAX_EXCERPT = 300
_MAX_EVIDENCE = 8
_REGION_LABEL = {'body': '본문', 'header': '머리말', 'footer': '꼬리말'}
_NUMBERED_LINE = re.compile(r'^\s*(?:\d{1,2}[.)]?|[가-하][.)])\s+\S')


def _evidence(unit: dict, relation: str, anchor: int = 0) -> dict | None:
    text = unit.get('text')
    if not isinstance(text, str) or not text.strip():
        return None
    start = max(0, min(anchor - 80, len(text) - _MAX_EXCERPT))
    end = min(len(text), start + _MAX_EXCERPT)
    return {
        'unitId': unit['id'], 'text': text[start:end], 'start': start,
        'end': end, 'relation': relation,
        'locator': deepcopy(unit.get('locator', {})),
    }


def _append(items: list, item: dict | None) -> None:
    if item and len(items) < _MAX_EVIDENCE:
        key = (item['unitId'], item['start'], item['end'], item['relation'])
        if all((x['unitId'], x['start'], x['end'], x['relation']) != key for x in items):
            items.append(item)


def _paragraphs(blocks: list) -> list[dict]:
    """Immediate cell paragraphs only; nested table cells have their own facts."""
    return [b for b in blocks if b.get('kind') == 'paragraph']


def _docx_locations(path: Path, inspection: dict, units: dict) -> dict:
    result = {}
    headings = {}
    previous = {}
    table_index = 0

    def refs(blocks: list) -> list[dict]:
        return [units[b['unitId']] for b in _paragraphs(blocks) if b['unitId'] in units]

    def walk(blocks: list, cell_context=None, depth=0):
        nonlocal table_index
        if depth > 24:
            raise ValueError('문서 구조의 중첩이 너무 깊습니다.')
        for block in blocks:
            region = block.get('region', 'body')
            if block.get('kind') == 'paragraph':
                unit = units.get(block.get('unitId'))
                if not unit:
                    continue
                evidence = []
                part = unit.get('locator', {}).get('part', region)
                heading = headings.get(part)
                if heading and heading['id'] != unit['id']:
                    _append(evidence, _evidence(heading, 'preceding_heading'))
                facts = {'region': region, 'evidence': evidence}
                if heading:
                    facts['section'] = heading['text'][:_MAX_EXCERPT]
                if cell_context:
                    facts['table'] = dict(cell_context['table'])
                    for relation, neighbours in cell_context['neighbours']:
                        for neighbour in neighbours:
                            if neighbour['id'] != unit['id']:
                                _append(evidence, _evidence(neighbour, relation))
                elif not block.get('heading'):
                    neighbour = previous.get(part)
                    if neighbour and (not heading or neighbour['id'] != heading['id']):
                        _append(evidence, _evidence(neighbour, 'preceding_paragraph'))
                result[unit['id']] = facts
                # A title in a table cell must never become the next cell's heading.
                if not cell_context:
                    if block.get('heading') and unit.get('text', '').strip():
                        headings[part] = unit
                    if unit.get('text', '').strip():
                        previous[part] = unit
                continue

            if block.get('kind') != 'table':
                continue
            # A paragraph preceding a table is not the immediate predecessor of
            # the prose following that table.
            previous.clear()
            table_index += 1
            index = table_index
            rows = block.get('rows', [])
            first_row = []
            col = 1
            for cell in rows[0] if rows else []:
                span = max(1, int(cell.get('colSpan', 1)))
                first_row.append((col, col + span, refs(cell.get('blocks', []))))
                col += span
            for row_index, row in enumerate(rows, 1):
                col = 1
                first_cell = refs(row[0].get('blocks', [])) if row else []
                for cell in row:
                    span = max(1, int(cell.get('colSpan', 1)))
                    same_cell = refs(cell.get('blocks', []))
                    # Only a first-row cell with the same grid start is supplied.
                    # A spanning title across other columns is not a column label.
                    above = next((items for a, b, items in first_row
                                  if a == col and b == col + span), []) if row_index > 1 else []
                    neighbours = [('same_cell', same_cell)]
                    if col > 1:
                        neighbours.append(('table_row_first_cell', first_cell))
                    if above:
                        neighbours.append(('table_first_row', above))
                    walk(cell.get('blocks', []), {
                        'table': {'index': index, 'row': row_index, 'column': col},
                        'neighbours': neighbours,
                    }, depth + 1)
                    col += span

    walk(build_layout(path, inspection.get('units', [])))
    return result


def _box(unit: dict):
    box = unit.get('locator', {}).get('bbox')
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if not all(type(n) in (int, float) and math.isfinite(n) for n in box):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _pdf_locations(inspection: dict, target_ids: set) -> dict:
    pages = {}
    for unit in inspection.get('units', []):
        if _box(unit) and type(unit.get('page')) is int:
            pages.setdefault(unit['page'], []).append(unit)
    result = {}
    for page, units in pages.items():
        for unit in units:
            if unit['id'] not in target_ids:
                continue
            x0, y0, x1, y1 = _box(unit)
            height = y1 - y0
            evidence = []
            left = []
            above = []
            numbered = []
            for other in units:
                if other['id'] == unit['id']:
                    continue
                ox0, oy0, ox1, oy1 = _box(other)
                overlap = max(0, min(y1, oy1) - max(y0, oy0))
                # Physical same row, not a semantic-role claim. Keep the far-left
                # label as well as the nearest cell; both carry their own locator.
                if ox1 <= x0 + 1 and overlap >= min(height, oy1 - oy0) * .5:
                    left.append(other)
                if 0 <= y0 - oy1 <= max(72, height * 4):
                    horizontal_overlap = min(x1, ox1) - max(x0, ox0)
                    if horizontal_overlap > 0 or abs(ox0 - x0) <= 12:
                        above.append(other)
                if (0 < y0 - oy1 <= 360 and ox0 <= x0 + 2
                        and len(other.get('text', '')) <= 80
                        and _NUMBERED_LINE.match(other.get('text', ''))):
                    numbered.append(other)
            left.sort(key=lambda u: _box(u)[0])
            for other in ([left[0], left[-1]] if len(left) > 1 else left):
                _append(evidence, _evidence(other, 'same_row_left'))
            if above:
                nearest = min(above, key=lambda u: (y0 - _box(u)[3], abs(x0 - _box(u)[0])))
                _append(evidence, _evidence(nearest, 'nearest_above'))
            if numbered:
                nearest = min(numbered, key=lambda u: y0 - _box(u)[3])
                _append(evidence, _evidence(nearest, 'preceding_numbered_line'))
            result[unit['id']] = {'region': 'body', 'page': page, 'evidence': evidence}
    return result


def attach_location_context(path: Path, inspection: dict, candidates: list[dict]) -> None:
    """Mutate only locationContext; preserve user decisions and exact locators.

    Context is omitted for unresolved, stale or mismatched candidate locations.
    Every excerpt is independently addressable as unit.text[start:end].
    """
    units = {u['id']: u for u in inspection.get('units', [])}
    fmt = inspection.get('format')
    if fmt == 'docx':
        locations = _docx_locations(Path(path), inspection, units)
    elif fmt == 'pdf':
        locations = _pdf_locations(inspection, {c.get('unitId') for c in candidates})
    else:
        locations = {}
    for candidate in candidates:
        candidate.pop('locationContext', None)
        unit = units.get(candidate.get('unitId'))
        start, end = candidate.get('start'), candidate.get('end')
        if (candidate.get('locationResolved') is not True or not unit
                or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(unit.get('text', ''))
                or unit['text'][start:end] != candidate.get('value')):
            continue
        facts = deepcopy(locations.get(unit['id'], {'region': 'body', 'evidence': []}))
        evidence = []
        _append(evidence, _evidence(unit, 'self', start))
        for item in facts.get('evidence', []):
            source = units.get(item['unitId'])
            if source and source['text'][item['start']:item['end']] == item['text']:
                _append(evidence, item)
        # Keep a locally established field label across later re-analysis, but
        # revalidate its literal range and locator against the original units.
        proof = candidate.get('labelEvidence')
        if isinstance(proof, dict):
            source = units.get(proof.get('unitId'))
            a, b = proof.get('start'), proof.get('end')
            if (source and type(a) is int and type(b) is int
                    and 0 <= a < b <= len(source['text'])
                    and source['text'][a:b] == proof.get('text')
                    and source.get('locator', {}) == proof.get('locator')):
                _append(evidence, deepcopy(proof))
        facts['evidence'] = evidence
        if fmt == 'pdf':
            facts['label'] = f"{unit.get('page', '?')}쪽"
        elif facts.get('table'):
            table = facts['table']
            facts['label'] = f"{_REGION_LABEL.get(facts['region'], '본문')} · 표 {table['index']} · {table['row']}행 {table['column']}열"
        else:
            facts['label'] = _REGION_LABEL.get(facts['region'], '본문')
        candidate['locationContext'] = facts
