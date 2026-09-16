"""Render safe DOCX structure as unit references, never raw HTML."""
from pathlib import Path
from .docx_io import W_NS, _safe_parse, read_package


def build_layout(path: Path, units: list[dict]) -> list[dict]:
    parts = read_package(path)['xml_parts']
    lookup = {(u['locator']['part'], u['locator']['p_index']): u for u in units}
    seen = set()
    output = []

    def tag(el):
        return el.tag if isinstance(el.tag, str) else ''

    def region(part):
        return 'header' if part.startswith('word/header') else 'footer' if part.startswith('word/footer') else 'body'

    order = sorted(p for p in parts if p.startswith('word/header') and p.endswith('.xml'))
    order += ['word/document.xml'] if 'word/document.xml' in parts else []
    order += sorted(p for p in parts if p.startswith('word/footer') and p.endswith('.xml'))
    for part in order:
        root = _safe_parse(parts[part])
        indices = {el: i for i, el in enumerate(root.iter(f'{{{W_NS}}}p'))}
        area = region(part)

        def walk(el, depth=0):
            if depth > 24:
                raise ValueError('문서 구조의 중첩이 너무 깊습니다.')
            name = tag(el)
            if name in {f'{{{W_NS}}}{n}' for n in ('drawing', 'pict', 'txbxContent')}:
                return []
            if name == f'{{{W_NS}}}p':
                unit = lookup.get((part, indices[el]))
                if not unit or unit['id'] in seen:
                    return []
                seen.add(unit['id'])
                style = el.find(f'{{{W_NS}}}pPr/{{{W_NS}}}pStyle')
                jc = el.find(f'{{{W_NS}}}pPr/{{{W_NS}}}jc')
                align = jc.get(f'{{{W_NS}}}val', 'left') if jc is not None else 'left'
                value = style.get(f'{{{W_NS}}}val', '') if style is not None else ''
                return [{'kind': 'paragraph', 'unitId': unit['id'], 'region': area,
                         'heading': value.startswith(('Title', 'Heading', '제목')),
                         'align': align if align in ('center', 'right') else 'left'}]
            if name == f'{{{W_NS}}}tbl':
                rows = []
                for row in el.findall(f'{{{W_NS}}}tr'):
                    cells = []
                    for cell in row.findall(f'{{{W_NS}}}tc'):
                        span = cell.find(f'{{{W_NS}}}tcPr/{{{W_NS}}}gridSpan')
                        try:
                            cols = int(span.get(f'{{{W_NS}}}val', '1')) if span is not None else 1
                        except ValueError:
                            cols = 1
                        cells.append({'colSpan': max(1, min(100, cols)), 'blocks': walk(cell, depth+1)})
                    rows.append(cells)
                return [{'kind': 'table', 'rows': rows, 'region': area}]
            return [b for child in el for b in walk(child, depth+1)]
        output.extend(walk(root))
    for unit in units:
        if unit['id'] not in seen:
            output.append({'kind': 'paragraph', 'unitId': unit['id'], 'heading': False,
                           'align': 'left', 'region': region(unit['locator']['part'])})
            seen.add(unit['id'])
    return output
