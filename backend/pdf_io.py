"""pdf_io.py — inspect PDFs and edit metadata only."""

import math
import re
from pathlib import Path
from lxml import etree

import fitz


RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XAP_NS = "http://ns.adobe.com/xap/1.0/"

INFO_FIELDS = [
    ("title", "title"),
    ("author", "author"),
    ("subject", "subject"),
    ("keywords", "keywords"),
    ("creator", "creator"),
    ("producer", "producer"),
    ("creationDate", "creationDate"),
    ("modDate", "modDate"),
]

_LATIN_THREADING_RE = re.compile(r"^\s*-?\d+\.?\d*\s*,?\s*-?\d+\.?\d*$")


def _parse_coord(line_dir, x0, y0, x1, y1):
    """PyMuPDF rawdict coordinates are already unrotated. Validate and return normalized boxes."""
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ValueError("글자 상자가 유효하지 않습니다.")
    ux = dx / length
    uy = dy / length
    if not math.isclose(abs(ux), 1.0, rel_tol=1e-3) and not math.isclose(abs(ux), 0.0, abs_tol=1e-3):
        uxx = ux / max(abs(ux), abs(uy))
        uyy = uy / max(abs(ux), abs(uy))
        if not math.isclose(uxx, 1.0, rel_tol=1e-2):
            raise ValueError("글자 배열이 수평이 아닙니다.")
    return [float(x0), float(y0), float(x1), float(y1)]


def _metadata(doc):
    entries = []
    refs = {}
    root = None
    info_fields = ['title', 'author', 'subject', 'keywords', 'creator', 'producer', 'creationDate', 'modDate']
    for field in info_fields:
        raw = doc.metadata.get(field, '')
        if isinstance(raw, str) and raw.strip():
            entries.append({'id': 'info:' + field, 'label': field, 'value': raw, 'source': 'PDF Info', 'action': 'delete'})
    xmp = doc.get_xml_metadata()
    if xmp:
        if '<!DOCTYPE' in xmp:
            raise ValueError('DOCTYPE이 포함된 XMP는 처리할 수 없습니다')
        try:
            root = etree.fromstring(xmp.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
        except Exception as e:
            raise ValueError(f'XMP 파싱 오류: {e}')
        descriptions = list(root.iter('{' + RDF_NS + '}Description'))
        for i, desc in enumerate(descriptions):
            seen_tags = set()
            for child in desc:
                tag = child.tag
                if not isinstance(tag, str):
                    continue
                if tag in seen_tags:
                    raise ValueError(f'중복된 XMP 속성: {tag}')
                seen_tags.add(tag)
                value = etree.tostring(child, method='c14n', with_comments=False).decode()
                eid = f'xmp:{i}:{tag}'
                entries.append({'id': eid, 'label': tag, 'value': value, 'source': 'XMP', 'action': 'delete'})
                refs[eid] = ('child', desc, child)
            for attr_key, attr_val in desc.attrib.items():
                eid = f'xmp:{i}:@{attr_key}'
                entries.append({'id': eid, 'label': attr_key, 'value': attr_val, 'source': 'XMP', 'action': 'delete'})
                refs[eid] = ('attr', desc, attr_key)
    return entries, root, refs


def metadata_items(doc):
    return _metadata(doc)[0]


def apply_metadata(doc, actions):
    entries, root, refs = _metadata(doc)
    entry_ids = {e['id'] for e in entries}
    for key, value in actions.items():
        if key not in entry_ids:
            raise ValueError(f'알 수 없는 액션 키: {key}')
        if value not in ('delete', 'keep'):
            raise ValueError(f'알 수 없는 액션 값: {value}')
    info_fields = ['title', 'author', 'subject', 'keywords', 'creator', 'producer', 'creationDate', 'modDate']
    info_dict = {}
    for field in info_fields:
        raw = doc.metadata.get(field, '')
        info_dict[field] = raw if isinstance(raw, str) else ''
    fields_to_clear = set()
    for entry in entries:
        if entry['source'] == 'PDF Info' and actions.get(entry['id'], 'delete') == 'delete':
            field = entry['id'][len('info:'):]
            if field in info_fields:
                fields_to_clear.add(field)
    for field in fields_to_clear:
        info_dict[field] = ''
    doc.set_metadata(info_dict)
    xmp_entries = [e for e in entries if e['source'] == 'XMP']
    if root is not None:
        if not xmp_entries or all(actions.get(e['id'], 'delete') == 'delete' for e in xmp_entries):
            doc.del_xml_metadata()
        else:
            for entry in xmp_entries:
                if actions.get(entry['id'], 'delete') == 'delete':
                    ref = refs.get(entry['id'])
                    if ref is None:
                        continue
                    ref_type, parent, key = ref
                    if ref_type == 'child':
                        parent.remove(key)
                    elif ref_type == 'attr':
                        del parent.attrib[key]
            xml_str = etree.tostring(root, encoding='unicode')
            doc.set_xml_metadata(xml_str)


def inspect(path, allow_empty=False):
    p = Path(path)
    if p.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('파일이 10MB를 초과합니다.')

    doc = fitz.open(path)
    try:
        if doc.is_pdf is False:
            raise ValueError('PDF가 아닙니다.')
        if doc.is_encrypted:
            raise ValueError('PDF가 암호화되어 있습니다.')
        if doc.page_count < 1 or doc.page_count > 100:
            raise ValueError('페이지 수가 1~100 범위를 벗어납니다.')

        fitz.TOOLS.set_small_glyph_heights(True)

        for xref in range(1, doc.xref_length()):
            try:
                obj = doc.xref_object(xref)
            except Exception:
                continue
            if '/JavaScript' in obj or '/JS' in obj:
                raise ValueError('JavaScript가 포함된 PDF는 지원하지 않습니다.')

        units = []
        uninspected = []
        pageSizes = []
        nonspace_count = 0

        for page in doc:
            page_idx = page.number + 1
            pageSizes.append({
                'width': page.cropbox.width,
                'height': page.cropbox.height,
                'rotation': page.rotation,
            })

            page_nonspace_count = 0
            blocks = page.get_text('rawdict')['blocks']
            for bi, block in enumerate(blocks):
                if block['type'] != 0:
                    continue
                lines = block.get('lines', [])
                for li, line in enumerate(lines):
                    d = line.get('dir', (1, 0))
                    dx = d[0] - 1.0
                    dy = d[1] - 0.0
                    if math.hypot(dx, dy) > 0.001:
                        raise ValueError('수평이 아닌 텍스트는 지원하지 않습니다.')

                    chars = []
                    for span in line.get('spans', []):
                        for ch in span.get('chars', []):
                            c = ch.get('c', '')
                            box = ch.get('bbox', [])
                            if not isinstance(c, str) or len(c) != 1:
                                raise ValueError('유효하지 않은 문자가 포함되어 있습니다.')
                            if not isinstance(box, (list, tuple)) or len(box) != 4:
                                raise ValueError('유효하지 않은 문자 상자가 포함되어 있습니다.')
                            try:
                                x0, y0, x1, y1 = map(float, box)
                            except (TypeError, ValueError):
                                raise ValueError('유효하지 않은 문자 상자가 포함되어 있습니다.')
                            if not (math.isfinite(x0) and math.isfinite(y0) and
                                    math.isfinite(x1) and math.isfinite(y1)):
                                raise ValueError('유효하지 않은 문자 상자가 포함되어 있습니다.')
                            if not (x1 > x0 and y1 > y0):
                                raise ValueError('유효하지 않은 문자 상자가 포함되어 있습니다.')
                            chars.append({'c': c, 'bbox': [x0, y0, x1, y1]})

                    if not chars:
                        continue

                    text = ''.join(ch['c'] for ch in chars)
                    id_str = f'p{page_idx}:b{bi}:l{li}'

                    minx = min(ch['bbox'][0] for ch in chars)
                    miny = min(ch['bbox'][1] for ch in chars)
                    maxx = max(ch['bbox'][2] for ch in chars)
                    maxy = max(ch['bbox'][3] for ch in chars)
                    locator_bbox = [minx, miny, maxx, maxy]

                    units.append({
                        'id': id_str,
                        'text': text,
                        'page': page_idx,
                        'locator': {'page': page_idx, 'bbox': locator_bbox},
                        'chars': chars,
                    })

                    line_nonspace = sum(1 for c in chars if not c['c'].isspace())
                    page_nonspace_count += line_nonspace
                    nonspace_count += line_nonspace

            if page_nonspace_count == 0:
                images = page.get_images()
                if images and not allow_empty:
                    raise ValueError('스캔 PDF는 지원하지 않습니다.')

            img_count = len(page.get_images())
            ann_count = len(list(page.annots() or []))
            link_count = len(page.get_links())
            widget_count = len(list(page.widgets() or []))

            parts = []
            if img_count > 0:
                parts.append(f'이미지 {img_count}개')
            if ann_count > 0:
                parts.append(f'주석 {ann_count}개')
            if link_count > 0:
                parts.append(f'링크 {link_count}개')
            if widget_count > 0:
                parts.append(f'위젯 {widget_count}개')
            if parts:
                uninspected.append(f'{page_idx}쪽: ' + ', '.join(parts))

        if doc.embfile_count() > 0:
            uninspected.append(f'첨부 파일 {doc.embfile_count()}개')

        if nonspace_count == 0 and not allow_empty:
            raise ValueError('텍스트가 없는 PDF는 지원하지 않습니다.')

        return {
            'format': 'pdf',
            'units': units,
            'metadata': metadata_items(doc),
            'uninspected': uninspected,
            'pageSizes': pageSizes,
        }
    finally:
        doc.close()
