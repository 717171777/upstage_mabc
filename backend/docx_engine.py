# -*- coding: utf-8 -*-
"""docx_engine: Korean privacy redaction for DOCX sharing-copy.

Exports
-------
inspect(path: Path) -> dict
render(source: Path, destination: Path, inspection: dict,
       candidates: list, metadata_actions: dict) -> dict
"""

from __future__ import annotations
import io, re, zipfile
from collections import defaultdict
from copy import deepcopy
from lxml import etree
from pathlib import Path

from .docx_io import (
    read_package, _safe_parse, inspect_metadata, get_units,
    find_uninspected, _sha256,
)

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CUSTOM_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/custom-properties'
APP_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'

_MASK_CHAR = '\u2588'  # █


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def inspect(path: Path) -> dict:
    """Inspect a DOCX file: return format, units, metadata, uninspected."""
    pkg = read_package(path)
    units = get_units(pkg)
    meta, meta_uninspected = inspect_metadata(pkg)
    feat_uninspected = find_uninspected(pkg)

    uninspected_labels: list[str] = []
    uninspected_labels.extend(meta_uninspected)
    uninspected_labels.extend(feat_uninspected)

    meta_out: list[dict] = []
    for m in meta:
        meta_out.append({
            'id': m['id'],
            'label': m['label'],
            'value': m['value'],
            'source': m['source'],
            'action': m['action'],
        })

    return {
        'format': 'docx',
        'units': units,
        'metadata': meta_out,
        'uninspected': uninspected_labels,
    }


def render(source, destination, inspection, candidates, metadata_actions):
    source = Path(source)
    dst = Path(destination)

    if source.resolve() == dst.resolve():
        raise ValueError('원본과 대상 경로가 동일합니다.')

    current_inspection = inspect(source)
    if current_inspection != inspection:
        raise ValueError('검사 정보가 일치하지 않습니다.')

    if not isinstance(inspection, dict) or inspection.get('format') != 'docx':
        raise ValueError('잘못된 inspection입니다.')

    if not source.is_file():
        raise ValueError('원본 파일을 찾을 수 없습니다.')

    source_sha = _sha256(source.read_bytes())

    cand_map = _validate_candidates(inspection, candidates)
    meta_actions = _normalize_meta_actions(inspection, metadata_actions)

    pkg = read_package(source)

    out_members = {}
    for fn, data in pkg['members'].items():
        out_members[fn] = data

    _apply_metadata_edits(out_members, pkg, meta_actions)
    _apply_text_edits(out_members, inspection, cand_map)

    _write_zip(out_members, dst)

    return _verify_render(source, dst, inspection, cand_map, meta_actions, source_sha)


# ---------------------------------------------------------------------------
# Candidate validation
# ---------------------------------------------------------------------------

def _validate_candidates(inspection: dict, candidates: list) -> dict:
    """Return id->candidate dict for fully validated candidates.

    Raises ValueError on any violation.
    """
    if not isinstance(candidates, list):
        raise ValueError('candidates는 리스트여야 합니다.')

    units_by_id = {u['id']: u for u in inspection.get('units', [])}
    seen_ids: set[str] = set()
    cand_map: dict[str, dict] = {}

    for c in candidates:
        if not isinstance(c, dict):
            raise ValueError('각 후보는 사전이어야 합니다.')
        cid = c.get('id')
        if cid is None:
            raise ValueError('후보에 id가 없습니다.')
        if cid in seen_ids:
            raise ValueError(f'후보 id가 중복됩니다: {cid}')
        seen_ids.add(cid)

        unit_id = c.get('unitId')
        if unit_id is None:
            raise ValueError(f'unitId가 없습니다: {cid}')
        if unit_id not in units_by_id:
            raise ValueError(f'유효하지 않은 unitId: {cid}')
        unit = units_by_id[unit_id]
        unit_text = unit['text']

        method = c.get('method', 'full')
        if method not in ('full', 'partial', 'delete', 'keep'):
            raise ValueError(f'유효하지 않은 method: {method} (후보 {cid})')

        location_resolved = c.get('locationResolved')
        if location_resolved is not True:
            raise ValueError(f'locationResolved가 true여야 합니다: {cid}')
        confirmed = c.get('confirmed')
        if confirmed is not True:
            raise ValueError(f'confirmed가 true여야 합니다: {cid}')

        start = c.get('start')
        end = c.get('end')
        if not isinstance(start, int) or isinstance(start, bool):
            raise ValueError(f'start는 정수여야 합니다: {cid}')
        if not isinstance(end, int) or isinstance(end, bool):
            raise ValueError(f'end는 정수여야 합니다: {cid}')
        if start < 0 or end <= start or end > len(unit_text):
            raise ValueError(f'start/end 범위가 잘못되었습니다: {cid}')
        value = c.get('value', '')
        if unit_text[start:end] != value:
            raise ValueError(f'value가 unit text 범위와 일치하지 않습니다: {cid}')

        # Overlap check: all candidates in same unit must not overlap
        for existing in cand_map.values():
            if existing['unitId'] == unit_id:
                es, ee = existing['start'], existing['end']
                if start < ee and end > es:
                    raise ValueError(
                        f'후보가 겹칩니다: unitId={unit_id}, '
                        f'{cid} [{start},{end}] vs {existing["id"]} [{es},{ee}]'
                    )

        # Partial mask validation
        if method == 'partial':
            mask = c.get('mask', [])
            if not isinstance(mask, list):
                raise ValueError(f'mask는 리스트여야 합니다: {cid}')
            merged = _merge_intervals(mask)
            value_len = len(value)
            for a, b in merged:
                if a < 0:
                    raise ValueError(
                        f'mask 구간이 음수로 시작합니다: {cid}'
                    )
                if b > value_len:
                    raise ValueError(
                        f'mask 구간이 value 길이를 초과합니다: {cid}'
                    )
            masked_len = sum(b - a for a, b in merged)
            if masked_len == 0:
                raise ValueError(
                    f'부분 마스킹의 경우 마스킹된 문자가 하나 이상 필요합니다: {cid}'
                )
            if masked_len == len(value):
                raise ValueError(
                    f'부분 마스킹의 경우 마스킹되지 않은 문자가 하나 이상 필요합니다: {cid}'
                )
            c_copy = dict(c)
            c_copy['_mask_merged'] = merged
            cand_map[cid] = c_copy
            continue

        cand_map[cid] = c

    return cand_map


def _merge_intervals(intervals: list) -> list[tuple[int, int]]:
    """Sort and merge adjacent/overlapping intervals."""
    if not intervals:
        return []
    for iv in intervals:
        if not isinstance(iv, (list, tuple)) or len(iv) != 2:
            raise ValueError(f'구간은 길이 2의 리스트/튜플이어야 합니다: {iv}')
        a, b = iv
        if not isinstance(a, int) or isinstance(a, bool):
            raise ValueError(f'구간 시작은 정수여야 합니다: {iv}')
        if not isinstance(b, int) or isinstance(b, bool):
            raise ValueError(f'구간 끝은 정수여야 합니다: {iv}')
        if a < 0:
            raise ValueError(f'구간 시작은 0 이상이어야 합니다: [{a},{b}]')
        if a >= b:
            raise ValueError(f'구간 시작이 끝보다 작아야 합니다: [{a},{b}]')
    sorted_iv = sorted(intervals, key=lambda x: (x[0], x[1]))
    merged: list[tuple[int, int]] = []
    for a, b in sorted_iv:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


# ---------------------------------------------------------------------------
# Metadata actions
# ---------------------------------------------------------------------------

def _normalize_meta_actions(inspection: dict, metadata_actions: dict) -> dict:
    """Return id->action dict for all discovered metadata.

    Unknown ids in metadata_actions raise ValueError.
    Missing ids default to 'delete'.
    """
    if metadata_actions is None:
        metadata_actions = {}
    result: dict[str, str] = {}
    known_ids = {m['id'] for m in inspection.get('metadata', [])}
    for mid, action in metadata_actions.items():
        if mid not in known_ids:
            raise ValueError(f'알 수 없는 메타데이터 id: {mid}')
        if action not in ('keep', 'delete'):
            raise ValueError(f'metadata action은 keep 또는 delete만 가능합니다: {mid}')
    for m in inspection.get('metadata', []):
        mid = m['id']
        action = metadata_actions.get(mid, 'delete')
        result[mid] = action
    return result


# ---------------------------------------------------------------------------
# Metadata editing on member bytes
# ---------------------------------------------------------------------------

def _apply_metadata_edits(members: dict, pkg: dict, actions: dict) -> None:
    """Mutate member bytes in-place using live element refs from inspect_metadata.

    Build id->element map once from inspect_metadata(pkg).  For delete:
      core/custom: element.parent.remove(element)
      app: element.text = None  (only non-typed; typed numeric/boolean/통계 유지)
    keep: no-op.  Finally serialize each root back into members.
    """
    meta_list, _ = inspect_metadata(pkg)

    # id -> element  (live references into pkg's parsed roots)
    id_to_elem: dict[str, etree._Element] = {}
    for m in meta_list:
        id_to_elem[m['id']] = m['element']

    # Which roots need re-serialization, and into which member key
    roots_to_serial: dict[str, tuple[etree._Element, str]] = {}

    for mid, action in actions.items():
        if action == 'keep':
            continue
        elem = id_to_elem.get(mid)
        if elem is None:
            raise ValueError(f'메타데이터 요소를 찾을 수 없습니다: {mid}')

        local = elem.tag.split('}', 1)[1] if '}' in elem.tag else elem.tag

        if mid.startswith('core|'):
            # core/custom: remove element from parent
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)
            root = pkg['core']
            if root is not None:
                roots_to_serial['docProps/core.xml'] = (root, 'docProps/core.xml')

        elif mid.startswith('custom|'):
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)
            root = pkg['custom']
            if root is not None:
                roots_to_serial['docProps/custom.xml'] = (root, 'docProps/custom.xml')

        elif mid.startswith('app|'):
            # app: only clear text for non-typed (string) properties;
            # typed numeric/boolean/통계 속성은 유지
            typed = False
            if isinstance(elem, dict):
                typed = elem.get('typed', False)
            else:
                # elem is an etree element; look up typed from meta_list
                for m in meta_list:
                    if m['element'] is elem:
                        typed = m.get('typed', False)
                        break
            if not typed:
                elem.text = None
            root = pkg['app']
            if root is not None:
                roots_to_serial['docProps/app.xml'] = (root, 'docProps/app.xml')
        else:
            raise ValueError(f'알 수 없는 메타데이터 id 형식: {mid}')

    # Serialize each dirty root back into members
    for member_key, (root, _) in roots_to_serial.items():
        members[member_key] = etree.tostring(
            root, xml_declaration=True, encoding='UTF-8', standalone=True
        )


def _delete_core_meta(members: dict, mid: str) -> None:
    """Delete a core metadata element by tag|ordinal id."""
    suffix = mid[5:]  # after 'core|'
    if 'docProps/core.xml' not in members:
        return
    data = members['docProps/core.xml']
    root = _safe_parse(data)
    tag, ord_str = suffix.split('|', 1)
    ordinal = int(ord_str)
    # Find nth child with matching tag
    count = 0
    target = None
    for child in root:
        ctag = child.tag.split('}', 1)[1] if '}' in child.tag else child.tag
        if ctag == tag:
            if count == ordinal - 1:
                target = child
                break
            count += 1
    if target is None:
        raise ValueError(f'메타데이터 요소를 찾을 수 없습니다: {mid}')
    parent = target.getparent()
    if parent is not None:
        parent.remove(target)
    members['docProps/core.xml'] = etree.tostring(
        root, xml_declaration=True, encoding='UTF-8', standalone=True
    )


def _delete_custom_meta(members: dict, mid: str) -> None:
    """Delete a custom property element by tag|ordinal id."""
    suffix = mid[7:]  # after 'custom|'
    if 'docProps/custom.xml' not in members:
        return
    data = members['docProps/custom.xml']
    root = _safe_parse(data)
    tag, ord_str = suffix.split('|', 1)
    ordinal = int(ord_str)
    # Find nth direct child with matching tag
    count = 0
    target = None
    for child in root:
        ctag = child.tag.split('}', 1)[1] if '}' in child.tag else child.tag
        if ctag == tag:
            if count == ordinal - 1:
                target = child
                break
            count += 1
    if target is None:
        raise ValueError(f'메타데이터 요소를 찾을 수 없습니다: {mid}')
    parent = target.getparent()
    if parent is not None:
        parent.remove(target)
    members['docProps/custom.xml'] = etree.tostring(
        root, xml_declaration=True, encoding='UTF-8', standalone=True
    )


def _delete_app_meta(members: dict, mid: str) -> None:
    """Delete an app metadata leaf value by setting its text to None.

    We keep the element so vector order is preserved; just clear the value.
    """
    path = mid[4:]  # after 'app|'
    if 'docProps/app.xml' not in members:
        return
    data = members['docProps/app.xml']
    root = _safe_parse(data)
    target = root.find(path)
    if target is None:
        raise ValueError(f'메타데이터 요소를 찾을 수 없습니다: {mid}')
    target.text = None
    members['docProps/app.xml'] = etree.tostring(
        root, xml_declaration=True, encoding='UTF-8', standalone=True
    )


# ---------------------------------------------------------------------------
# Text editing on member bytes
# ---------------------------------------------------------------------------

def _apply_text_edits(members: dict, inspection: dict, cand_map: dict) -> None:
    """Apply all candidate edits to XML parts in members dict.

    Caller passes the actual part_name for each group (from unit locator).
    Members keys use 'word/document.xml' style (slash stripped) so we
    normalize the locator part when looking up members.
    """
    # Group candidates by part
    by_part: dict[str, list[dict]] = defaultdict(list)
    for cid, c in cand_map.items():
        if c['method'] == 'keep':
            continue
        unit_id = c['unitId']
        unit = next((u for u in inspection['units'] if u['id'] == unit_id), None)
        if unit is None:
            raise ValueError(f'단위 요소를 찾을 수 없습니다: {unit_id}')
        part = unit['locator']['part']
        by_part[part].append(c)

    for part_name, cands in by_part.items():
        # Members keys use 'word/document.xml' (no leading slash)
        member_key = part_name.lstrip('/')
        if member_key not in members:
            raise ValueError(f'부품을 찾을 수 없습니다: {member_key}')
        data = members[member_key]
        edited = _edit_part_xml(data, cands, part_name, inspection)
        members[member_key] = edited


def _edit_part_xml(xml_bytes: bytes, cands: list[dict], part_name: str,
                   inspection: dict) -> bytes:
    """Parse part XML, apply edits to runs, serialize back.

    part_name: actual member key (e.g. 'word/header1.xml'), passed by caller.
    """
    root = _safe_parse(xml_bytes)

    # Build mapping: unit_id -> p_element for the given part only
    unit_to_p: dict[str, etree._Element] = {}
    for u in inspection['units']:
        if u['locator']['part'] != part_name:
            continue
        pidx = u['locator']['p_index']
        p_el = _find_p_by_index(root, pidx)
        if p_el is not None:
            unit_to_p[u['id']] = p_el

    # Group candidates by unit
    by_unit: dict[str, list[dict]] = defaultdict(list)
    for c in cands:
        by_unit[c['unitId']].append(c)

    for uid, unit_cands in by_unit.items():
        p_el = unit_to_p.get(uid)
        if p_el is None:
            raise ValueError(
                f'단위 요소(pid)를 XML에서 찾을 수 없습니다: {uid} ({part_name})'
            )
        _edit_paragraph(p_el, unit_cands)

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _part_name_for_root(root: etree._Element) -> str:
    """Heuristically identify part name from root element context.

    We rely on caller to pass the right part; here we just return a sentinel.
    Actually, we get part name from the inspection units list.
    """
    # This function is called with root from a known part.
    # The caller already knows the part name.
    # But to be safe, we check if it's document, header, footer
    tag = root.tag
    if tag == f'{{{W_NS}}}document':
        return 'word/document.xml'
    if tag == f'{{{W_NS}}}hdr':
        return 'word/header1.xml'  # approximation
    if tag == f'{{{W_NS}}}ftr':
        return 'word/footer1.xml'
    # Fallback: search inspection units for matching p_index pattern
    return 'unknown'


def _find_p_by_index(root: etree._Element, index: int) -> etree._Element | None:
    """Find the n-th w:p element in document order."""
    cnt = 0
    for p in root.iter(f'{{{W_NS}}}p'):
        if cnt == index:
            return p
        cnt += 1
    return None


# ---------------------------------------------------------------------------
# Paragraph run editing
# ---------------------------------------------------------------------------

def _edit_paragraph(p_el, cands):
    edits = []
    for c in cands:
        m = c['method']
        if m == 'full':
            edits.append((c['start'], c['end'], _MASK_CHAR * (c['end'] - c['start'])))
        elif m == 'delete':
            edits.append((c['start'], c['end'], ''))
        elif m == 'keep':
            pass
        elif m == 'partial':
            for a, b in c['_mask_merged']:
                edits.append((c['start'] + a, c['start'] + b, _MASK_CHAR * (b - a)))
        else:
            raise ValueError('알 수 없는 메서드')
    edits.sort(key=lambda x: x[0], reverse=True)
    for start, end, repl in edits:
        runs = []
        off = 0
        for r in p_el.iter(f'{{{W_NS}}}r'):
            if _is_run_in_nested_p(r, p_el):
                continue
            rt = ''
            for ch in r:
                t = ch.tag
                if t == f'{{{W_NS}}}t':
                    rt += ch.text or ''
                elif t == f'{{{W_NS}}}tab':
                    rt += '\t'
                elif t in (f'{{{W_NS}}}br', f'{{{W_NS}}}cr'):
                    rt += '\n'
            rs = off
            re = off + len(rt)
            runs.append((r, rs, re, rt))
            off = re
        para_len = off
        assert 0 <= start < end <= para_len, '편집 범위 오류'
        overlapping = [(r, rs, re, rt) for r, rs, re, rt in runs if re > start and rs < end]
        assert overlapping, '겹치는 런 없음'
        for r, rs, re, rt in overlapping:
            ls = max(start, rs) - rs
            le = min(end, re) - rs
            # Replace within each original run to preserve its character count
            # and formatting; the saved XML contains no masked source text.
            newtext = rt[:ls] + (_MASK_CHAR * (le - ls) if repl else '') + rt[le:]
            text_tags = {f'{{{W_NS}}}t', f'{{{W_NS}}}tab', f'{{{W_NS}}}br', f'{{{W_NS}}}cr'}
            child_idx = None
            text_children = []
            for i, ch in enumerate(r):
                if ch.tag in text_tags:
                    if child_idx is None:
                        child_idx = i
                    text_children.append(ch)
            for ch in text_children:
                r.remove(ch)
            elems = []
            buf = ''
            for ch in newtext:
                if ch == '\t':
                    if buf:
                        t_el = etree.Element(f'{{{W_NS}}}t')
                        t_el.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                        t_el.text = buf
                        elems.append(t_el)
                        buf = ''
                    elems.append(etree.Element(f'{{{W_NS}}}tab'))
                elif ch == '\n':
                    if buf:
                        t_el = etree.Element(f'{{{W_NS}}}t')
                        t_el.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                        t_el.text = buf
                        elems.append(t_el)
                        buf = ''
                    elems.append(etree.Element(f'{{{W_NS}}}br'))
                else:
                    buf += ch
            if buf:
                t_el = etree.Element(f'{{{W_NS}}}t')
                t_el.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                t_el.text = buf
                elems.append(t_el)
            if child_idx is None:
                for el in elems:
                    r.append(el)
            else:
                for i, el in enumerate(elems):
                    r.insert(child_idx + i, el)

        # keep: no-op


def _text_of(child_elt) -> str:
    local = child_elt.tag.split('}', 1)[1] if '}' in child_elt.tag else child_elt.tag
    if local == 't':
        return child_elt.text or ''
    if local == 'tab':
        return '\t'
    if local in ('br', 'cr'):
        return '\n'
    return ''


def _replace_span_xml(runs, run_maps, run_span, start, end, repl_full,
                      mask_merged, is_partial):
    """Replace the span [start,end) with repl_full, then for partial also mask."""
    # find first run overlapping start
    target_ri = None
    offset_in_run = None
    for ri, gs, ge in run_span:
        if ge <= start:
            continue
        if gs >= end:
            break
        target_ri = ri
        offset_in_run = max(start, gs) - gs
        break
    if target_ri is None:
        return

    run = runs[target_ri]
    rm = run_maps[target_ri]

    # original span text in this run
    seg_start = max(start, rm[0][0]) - rm[0][0] if rm else 0
    seg_end = min(end, rm[-1][1]) - rm[0][0] if rm else 0

    # Build new text for first affected run:
    # prefix (before start within this run) + replacement + suffix (after end within this run)
    # We only edit the first run; later runs get character removal only.

    # Collect all text in the run before and after the span (global positions relative to run)
    run_text = _run_text_of_run(run, rm)

    # Compute span range within run text
    run_start_in_span = max(start, rm[0][0]) - rm[0][0]
    run_end_in_span = min(end, rm[-1][1]) - rm[0][0]

    before = run_text[:run_start_in_span]
    after = run_text[run_end_in_span:]

    # Replace only the selected span with the supplied masking text.

    new_run_text = before + repl_full + after

    # Rewrite run text children
    _rewrite_run_text(run, new_run_text)

    # For later runs overlapping the span, remove characters in [start,end)
    for ri in range(target_ri + 1, len(runs)):
        gs, ge = run_span[ri][1], run_span[ri][2]
        if ge <= start or gs >= end:
            continue
        _remove_span_from_run(runs[ri], run_maps[ri], start, end)


def _delete_span_xml(runs, run_maps, run_span, start, end):
    # find first run overlapping start, remove span from it and later runs
    target_ri = None
    for ri, gs, ge in run_span:
        if ge <= start:
            continue
        target_ri = ri
        break
    if target_ri is None:
        return

    _remove_span_from_run(runs[target_ri], run_maps[target_ri], start, end)
    for ri in range(target_ri + 1, len(runs)):
        gs, ge = run_span[ri][1], run_span[ri][2]
        if ge <= start or gs >= end:
            continue
        _remove_span_from_run(runs[ri], run_maps[ri], start, end)


def _run_text_of_run(run, rm):
    return ''.join(_text_of(ch) for _, _, ch, _ in rm)


def _rewrite_run_text(run, new_text):
    """Replace text-bearing children of run with a single w:t preserving xml:space.

    rPr and non-text children are preserved.
    """
    # Remove existing text children (w:t, w:tab, w:br, w:cr)
    for ch in list(run):
        local = ch.tag.split('}', 1)[1] if '}' in ch.tag else ch.tag
        if local in ('t', 'tab', 'br', 'cr'):
            run.remove(ch)
    # Create new w:t
    nsmap = run.nsmap
    w_ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    t = etree.SubElement(run, f'{{{w_ns}}}t')
    t.set('{http://www.w3.org/xml/1998/namespace}space', 'preserve')
    t.text = new_text


def _remove_span_from_run(run, rm, start, end):
    """Remove characters in [start,end) from this run's text children."""
    run_text = _run_text_of_run(run, rm)
    before = run_text[:max(start, rm[0][0]) - rm[0][0]]
    after = run_text[min(end, rm[-1][1]) - rm[0][0]:]
    new_text = before + after
    _rewrite_run_text(run, new_text)


def _build_replacement(method: str, value: str, mask_merged: list) -> str:
    """Build a same-character-count replacement, keeping visible partial text."""
    if method == 'full':
        return _MASK_CHAR * len(value)
    if method == 'delete':
        return ''
    if method == 'partial':
        chars = list(value)
        for a, b in mask_merged:
            chars[a:b] = [_MASK_CHAR] * (b - a)
        return ''.join(chars)
    return value


def _apply_delete(segments: list[dict], start: int, end: int) -> None:
    """Remove characters in [start, end) from segments."""
    # Find affected segments and trim/remove them
    affected: list[int] = []
    for i, seg in enumerate(segments):
        if seg['ge'] <= start or seg['gs'] >= end:
            continue
        affected.append(i)

    if not affected:
        return

    # Process from last to first to keep indices stable
    for i in reversed(affected):
        seg = segments[i]
        seg_start_in_span = max(start, seg['gs']) - seg['gs']
        seg_end_in_span = min(end, seg['ge']) - seg['gs']
        seg['text'] = seg['text'][:seg_start_in_span] + seg['text'][seg_end_in_span:]
        seg['ge'] = seg['gs'] + len(seg['text'])

    # Remove empty segments and update global positions
    _renumber_segments(segments)


def _apply_replace(segments: list[dict], start: int, end: int,
                   repl: str) -> None:
    """Replace characters in [start, end) with repl text."""
    # Find the segment containing 'start'
    first_idx = None
    for i, seg in enumerate(segments):
        if seg['gs'] <= start < seg['ge']:
            first_idx = i
            break
    if first_idx is None:
        return  # shouldn't happen for valid spans

    # We'll replace across segments. The replacement text goes into
    # the first affected child at offset (start - seg['gs']).
    # Then remaining repl chars overflow into subsequent segments.

    repl_remaining = repl
    seg_idx = first_idx

    while seg_idx < len(segments) and repl_remaining:
        seg = segments[seg_idx]
        if seg['ge'] <= start:
            seg_idx += 1
            continue
        if seg['gs'] >= end:
            break

        # This segment overlaps the span
        seg_start_in_span = max(start, seg['gs']) - seg['gs']
        seg_end_in_span = min(end, seg['ge']) - seg['gs']
        seg_len = seg_end_in_span - seg_start_in_span

        if seg_idx == first_idx:
            # First affected segment: insert repl at offset, keep prefix
            offset = start - seg['gs']
            new_text = seg['text'][:offset] + repl_remaining + seg['text'][offset + seg_len:]
            seg['text'] = new_text
            seg['ge'] = seg['gs'] + len(seg['text'])
            # All repl consumed in first segment
            break
        else:
            # Subsequent segments: replace the entire overlapping part
            new_text = seg['text'][:seg_start_in_span] + repl_remaining + seg['text'][seg_end_in_span:]
            seg['text'] = new_text
            seg['ge'] = seg['gs'] + len(seg['text'])
            # Only consume part of repl that fits in this segment's original length
            consumed = seg_len
            repl_remaining = repl_remaining[consumed:]
            seg_idx += 1

    _renumber_segments(segments)


def _renumber_segments(segments: list[dict]) -> None:
    """Remove empty segments and renumber global positions."""
    # Remove empty text segments
    segments[:] = [s for s in segments if s['text']]
    # Renumber
    pos = 0
    for seg in segments:
        seg['gs'] = pos
        pos += len(seg['text'])
        seg['ge'] = pos


def _is_run_in_nested_p(run: etree._Element, top_p: etree._Element) -> bool:
    """Check if run is inside a w:p that is a descendant of top_p but not top_p."""
    parent = run.getparent()
    while parent is not None:
        if parent is top_p:
            return False
        local = parent.tag.split('}', 1)[1] if '}' in parent.tag else parent.tag
        if local == 'p':
            return True
        parent = parent.getparent()
    return False


# ---------------------------------------------------------------------------
# ZIP writing
# ---------------------------------------------------------------------------

def _write_zip(members: dict, dst: Path) -> None:
    """Write members dict to a ZIP file at dst."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for fn, data in members.items():
            # Preserve original ZipInfo for compression type etc.
            # We write with default compression; original compression info lost.
            zout.writestr(fn, data)
    buf.seek(0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(buf.read())


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _verify_render(source: Path, destination: Path, inspection: dict,
                   cand_map: dict, meta_actions: dict,
                   source_sha: str) -> dict:
    """Re-open output, verify paragraphs, metadata, and original hash."""
    dst = Path(destination)
    if not dst.is_file():
        return {'passed': False, 'checks': [{'name': '출력 파일 없음', 'passed': False}],
                'sha256': ''}

    checks: list[dict] = []
    passed_all = True

    # Re-inspect output
    try:
        out_inspection = inspect(dst)
    except ValueError as e:
        checks.append({'name': f'출력 파일 inspect 실패: {e}', 'passed': False})
        passed_all = False
        return {'passed': False, 'checks': checks, 'sha256': _sha256(dst.read_bytes())}

    # Verify every supported paragraph
    for u in inspection['units']:
        out_u = next((x for x in out_inspection['units'] if x['id'] == u['id']), None)
        if out_u is None:
            checks.append({'name': f'단락 누락: {u["id"]}', 'passed': False})
            passed_all = False
            continue

        # Compute expected text from original + edits
        expected = _compute_expected_text(u, cand_map)
        if out_u['text'] != expected:
            checks.append({
                'name': f'단락 텍스트 불일치: {u["id"]}',
                'passed': False,
            })
            passed_all = False

    # Verify metadata
    out_meta = {m['id']: m['value'] for m in out_inspection['metadata']}
    for m in inspection['metadata']:
        mid = m['id']
        action = meta_actions.get(mid, 'delete')
        if action == 'keep':
            if mid not in out_meta or out_meta[mid] != m['value']:
                checks.append({
                    'name': f'메타데이터 유지 실패: {mid}',
                    'passed': False,
                })
                passed_all = False
        else:  # delete
            if mid in out_meta:
                checks.append({
                    'name': f'메타데이터 삭제 실패: {mid}',
                    'passed': False,
                })
                passed_all = False

    # Verify original file unchanged
    if source_sha != _sha256(source.read_bytes()):
        checks.append({'name': '원본 파일이 변경되었습니다', 'passed': False})
        passed_all = False

    if not passed_all:
        try:
            dst.unlink()
        except Exception:
            pass
        return {'passed': False, 'checks': checks,
                'sha256': _sha256(dst.read_bytes()) if dst.is_file() else ''}

    return {
        'passed': True,
        'checks': checks,
        'sha256': _sha256(dst.read_bytes()),
    }


def _compute_expected_text(unit, cand_map):
    cands = [c for c in cand_map.values() if c['unitId'] == unit['id']]
    edits = []
    for c in cands:
        m = c['method']
        if m == 'full':
            edits.append((c['start'], c['end'], _MASK_CHAR * (c['end'] - c['start'])))
        elif m == 'delete':
            edits.append((c['start'], c['end'], ''))
        elif m == 'keep':
            pass
        elif m == 'partial':
            for a, b in c['_mask_merged']:
                edits.append((c['start'] + a, c['start'] + b, _MASK_CHAR * (b - a)))
        else:
            raise ValueError('알 수 없는 메서드')
    edits.sort(key=lambda x: x[0], reverse=True)
    text = unit['text']
    for start, end, repl in edits:
        text = text[:start] + repl + text[end:]
    return text

