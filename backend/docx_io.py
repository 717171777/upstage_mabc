# -*- coding: utf-8 -*-
"""docx_io: low-level DOCX package reading, XML parsing, metadata inspection.

Exports
-------
read_package(path) -> dict
    Context-managed read of entire ZIP; returns member bytes + metadata.
parse_xml(xml_bytes) -> etree._Element
    Safe XML parse with entity/network blocking and DOCTYPE rejection.
inspect_metadata(root_elements) -> list[dict]
    Discover core/custom/app metadata leaves with stable ids.
"""

from __future__ import annotations
import hashlib, io, re, zipfile
from collections import OrderedDict
from collections import defaultdict
from lxml import etree
from pathlib import Path

_MAX_INPUT = 10 * 1024 * 1024
_MAX_ENTRIES = 2000
_MAX_UNCOMP = 100 * 1024 * 1024
_REQUIRED = frozenset({'[Content_Types].xml', 'word/document.xml'})

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
CP_NS = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
DC_NS = 'http://purl.org/dc/elements/1.1/'
CUSTOM_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/custom-properties'
VT_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'
APP_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _safe_parse(xml: bytes) -> etree._Element:
    """Parse XML with strict security: no entities, no network, no DTD.

    Raises ValueError on any parse failure or DOCTYPE presence.
    """
    if b'<!DOCTYPE' in xml:
        raise ValueError('DOCTYPE 선언이 포함된 문서는 처리할 수 없습니다.')
    try:
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            strip_cdata=False,
            recover=False,
        )
        return etree.fromstring(xml, parser)
    except Exception as e:
        raise ValueError(f'XML 구문 분석 오류: {e}') from e


def _has_tracked(xml: bytes) -> bool:
    """Check a single XML blob for tracked-change tags.

    Fails closed: parse error => True (block the package).
    """
    try:
        root = _safe_parse(xml)
    except ValueError:
        return True  # malformed XML blocks the package
    except Exception:
        return True
    tracked_local = {
        'ins', 'del', 'delText',
        'moveFrom', 'moveTo',
        'moveFromRangeStart', 'moveFromRangeEnd',
        'moveToRangeStart', 'moveToRangeEnd',
    }
    for el in root.iter():
        local = el.tag.split('}', 1)[1] if '}' in el.tag else el.tag
        if local in tracked_local:
            return True
        if local.endswith('PrChange'):
            return True
        if local.endswith('tblGridChange'):
            return True
        if local in ('cellIns', 'cellDel', 'cellMerge'):
            return True
    return False


def _is_simple_page_field(field: etree._Element) -> bool:
    """Recognize a numeric page field without interpreting arbitrary field code.

    Only direct run/text children are accepted. Formatting or extension nodes
    outside that deliberately small shape still require manual inspection.
    This is a read-only exception; it never rewrites or evaluates the field.
    """
    w = f'{{{W_NS}}}'
    if field.tag != w + 'fldSimple':
        return False
    instruction = field.get(w + 'instr', '')
    if not re.fullmatch(
        r'[ \t\r\n]*(?:PAGE|NUMPAGES|SECTIONPAGES)'
        r'(?:[ \t\r\n]+\\\*[ \t\r\n]+MERGEFORMAT)?[ \t\r\n]*',
        instruction, re.IGNORECASE | re.ASCII,
    ):
        return False
    for name, value in field.attrib.items():
        if name == w + 'instr':
            continue
        if name not in (w + 'dirty', w + 'fldLock') or value not in ('true', 'false', '1', '0', 'on', 'off'):
            return False
    if field.text and field.text.strip():
        return False
    cache = []
    for run in field:
        if run.tag != w + 'r' or (run.text and run.text.strip()) or (run.tail and run.tail.strip()):
            return False
        for name, value in run.attrib.items():
            if name not in (w + 'rsidR', w + 'rsidRPr', w + 'rsidDel') or not re.fullmatch(r'[0-9A-Fa-f]{8}', value):
                return False
        for text in run:
            if text.tag != w + 't' or len(text) or (text.tail and text.tail.strip()):
                return False
            if any(name != '{http://www.w3.org/XML/1998/namespace}space' or value not in ('default', 'preserve')
                   for name, value in text.attrib.items()):
                return False
            cache.append(text.text or '')
    return bool(re.fullmatch(r'[0-9]*', ''.join(cache)))


def _find_uninspected(xml: bytes) -> list[str]:
    """Return feature labels found in XML that we cannot redact.

    Parse error => empty (not uninspected; just unreadable for this scan).
    """
    try:
        root = _safe_parse(xml)
    except Exception:
        return []
    found: set[str] = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = el.tag.split('}', 1)[1] if '}' in el.tag else el.tag
        if local in ('footnote', 'endnote', 'commentReference'):
            found.add('주석/각주')
        elif local in ('txbxContent',):
            found.add('텍스트 상자')
        elif local in ('drawing', 'pict', 'blip'):
            found.add('그림/그리기')
        elif local in ('media',):
            found.add('미디어')
        elif local in ('externalLink', 'hyperlink'):
            found.add('외부 연결')
        elif local == 'fldSimple':
            if not _is_simple_page_field(el):
                found.add('필드')
        elif local in ('instrText', 'fldChar'):
            found.add('필드')
    return sorted(found)


def read_package(path: Path) -> dict:
    """Read and validate a DOCX ZIP package.

    Returns dict with keys:
        members: OrderedDict filename->bytes (all entries)
        xml_parts: OrderedDict filename->bytes (XML only)
        zip_info: list of zipfile.ZipInfo
        sha256: hex digest of raw file bytes
        core: parsed core.xml root or None
        custom: parsed custom.xml root or None
        app: parsed app.xml root or None
    """
    p = Path(path)
    if not p.is_file():
        raise ValueError('DOCX 파일을 찾을 수 없습니다.')
    raw = p.read_bytes()
    if len(raw) > _MAX_INPUT:
        raise ValueError('DOCX 파일이 너무 큽니다 (최대 10MB).')
    sha = _sha256(raw)

    try:
        zf = zipfile.ZipFile(p, 'r')
    except Exception as e:
        raise ValueError(f'DOCX 형식이 올바르지 않습니다: {e}') from e

    infos = zf.infolist()
    if len(infos) > _MAX_ENTRIES:
        zf.close()
        raise ValueError('DOCX 항목 수가 너무 많습니다 (최대 2000개).')

    # encrypted check: flag bit 0 (general purpose bit 0)
    for info in infos:
        if info.flag_bits & 0x1:
            zf.close()
            raise ValueError('암호화된 DOCX는 처리할 수 없습니다.')

    # path safety
    seen: set[str] = set()
    for info in infos:
        fn = info.filename
        if fn in seen:
            zf.close()
            raise ValueError(f'DOCX에 중복 경로가 있습니다: {fn}')
        seen.add(fn)
        if fn.startswith('/') or fn.startswith('\\'):
            zf.close()
            raise ValueError(f'절대 경로가 포함된 잘못된 경로: {fn}')
        if '..' in fn or '\\' in fn:
            zf.close()
            raise ValueError(f'경로 탐색이 포함된 잘못된 경로: {fn}')

    # uncompressed size: use file_size (actual uncompressed), not compress_size
    total_uncomp = 0
    for info in infos:
        total_uncomp += info.file_size
        if info.file_size > _MAX_UNCOMP:
            zf.close()
            raise ValueError(f'압축 해제 크기가 너무 큰 파일: {info.filename}')
    if total_uncomp > _MAX_UNCOMP * 2:  # generous total bound
        zf.close()
        raise ValueError('DOCX 전체 압축 해제 크기가 너무 큽니다.')

    # required entries
    names = {i.filename for i in infos}
    missing = _REQUIRED - names
    if missing:
        zf.close()
        raise ValueError(f'필수 항목이 없습니다: {", ".join(sorted(missing))}')

    # read members inside context
    members = OrderedDict()
    xml_parts = OrderedDict()
    core_root = None
    custom_root = None
    app_root = None

    try:
        for info in infos:
            data = zf.read(info.filename)
            members[info.filename] = data
            if info.filename.lower().endswith('.xml'):
                xml_parts[info.filename] = data

        # parse metadata XMLs
        if 'docProps/core.xml' in members:
            try:
                core_root = _safe_parse(members['docProps/core.xml'])
            except ValueError:
                pass
        if 'docProps/custom.xml' in members:
            try:
                custom_root = _safe_parse(members['docProps/custom.xml'])
            except ValueError:
                pass
        if 'docProps/app.xml' in members:
            try:
                app_root = _safe_parse(members['docProps/app.xml'])
            except ValueError:
                pass
    finally:
        zf.close()

    # tracked changes across ALL XML
    for fn, data in xml_parts.items():
        if _has_tracked(data):
            raise ValueError(
                f'추적 변경 항목이 있어 처리할 수 없습니다: {fn}. '
                '변경 내용을 모두 수락/거부한 후 다시 시도하세요.'
            )

    # macros / embedded OLE
    for info in infos:
        fn_l = info.filename.lower()
        bad_exts = ('.bin', '.exe', '.dll', '.ole', '.xls', '.xltm', '.dotm',
                    '.xlam', '.xlsm', '.xltx', '.xlt', '.xlsb')
        if any(fn_l.endswith(ext) for ext in bad_exts):
            raise ValueError(
                f'매크로 또는 OLE 개체가 포함되어 있어 처리할 수 없습니다: {info.filename}'
            )
        if 'vbaProject' in fn_l:
            raise ValueError(
                f'매크로가 포함되어 있어 처리할 수 없습니다: {info.filename}'
            )

    return {
        'members': members,
        'xml_parts': xml_parts,
        'zip_info': infos,
        'sha256': sha,
        'core': core_root,
        'custom': custom_root,
        'app': app_root,
    }


# ---------------------------------------------------------------------------
# Metadata inspection
# ---------------------------------------------------------------------------

def _elem_ordinal(root: etree._Element, el: etree._Element) -> str:
    """Stable id suffix for a direct child element: tag|ordinal.

    Ordinal is the 1-based position among siblings with the same tag.
    This avoids XPath namespace issues.
    """
    tag = el.tag.split('}', 1)[1] if '}' in el.tag else el.tag
    parent = el.getparent()
    if parent is None:
        return tag
    # Count preceding siblings with same tag
    ord_idx = 1
    for sib in parent:
        sib_tag = sib.tag.split('}', 1)[1] if '}' in sib.tag else sib.tag
        if sib_tag == tag:
            if sib is el:
                break
            ord_idx += 1
    return f'{tag}|{ord_idx}'


def _core_meta(root: etree._Element) -> list[dict]:
    """Each direct child of coreProperties => metadata leaf.

    id = 'core'|tag|ordinal.  label = local tag name.  value = text or ''.
    동일 태그가 두 번 이상 나타나면 구조 오류로 간주해 block.
    """
    out: list[dict] = []
    if root is None:
        return out
    tag_counts: dict[str, int] = {}
    for child in root:
        tag = child.tag.split('}', 1)[1] if '}' in child.tag else child.tag
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
    for child in root:
        tag = child.tag.split('}', 1)[1] if '}' in child.tag else child.tag
        if tag_counts[tag] > 1:
            raise ValueError(
                f'메타데이터 구조 오류: core.xml에 동일 태그 {tag}가 '
                f'{tag_counts[tag]}개 존재하여 어떤 요소인지 판별할 수 없습니다.'
            )
        val = child.text or ''
        suffix = _elem_ordinal(root, child)
        mid = f'core|{suffix}'
        out.append({
            'id': mid,
            'label': tag,
            'value': val,
            'source': 'docProps/core.xml',
            'action': 'delete',
            'element': child,  # live reference for deletion
        })
    return out



def _custom_meta(root):
    if root is None:
        return []
    
    seen_pids = set()
    result = []
    
    for prop in root.findall(f'{{{CUSTOM_NS}}}property'):
        pid_str = prop.get('pid')
        if pid_str is None:
            raise ValueError("pid 속성이 없습니다")
        
        try:
            pid = int(pid_str)
        except (ValueError, TypeError):
            raise ValueError(f"pid 속성이 유효한 정수가 아닙니다: {pid_str}")
        
        if pid < 2:
            raise ValueError(f"pid는 2 이상이어야 합니다: {pid}")
        
        if pid in seen_pids:
            raise ValueError(f"중복된 pid입니다: {pid}")
        
        seen_pids.add(pid)
        
        children = list(prop)
        if len(children) == 1:
            child = children[0]
            local_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            
            if local_tag in ('lpwstr', 'lpstr', 'bool', 'i4', 'r8', 'date', 'filetime', 'cy', 'decimal'):
                result.append({
                    'id': f'custom|pid={pid}',
                    'label': prop.get('name') or 'property',
                    'value': child.text or '',
                    'source': 'docProps/custom.xml',
                    'action': 'delete',
                    'element': prop
                })
    
    return result


def _app_meta(root: etree._Element) -> list[dict]:
    """개인정보 가능 문자열 속성만 삭제 후보로 추출.

    통계/boolean/typed 속성은 후보에서 제외하고, 빈 텍스트 요소도 ordinal에는
    포함시켜 앞 요소 삭제 후 뒤 keep 요소 id가 변하지 않도록 한다.
    value는 원문 텍스트를 그대로 반환하며, 빈 값 판별에만 strip을 쓴다.
    """
    _STRING_TAGS = frozenset({
        'Company', 'Manager', 'Template', 'HyperlinkBase', 'Application',
        'AppVersion', 'Title', 'Subject', 'Author', 'Keywords', 'Comments',
        'LastModifiedBy', 'Category', 'Description', 'ContentStatus',
        'Created', 'Modified', 'RevisionNumber', 'DocumentVersion',
        'ContentType', 'PageCount',  # PageCount는 문자열 표현이면 포함 가능
    })
    _VT_STRING = frozenset({'lpstr', 'lpwstr'})

    out: list[dict] = []
    if root is None:
        return out

    # 1. 문서 순서 전체 리프(빈 텍스트 포함) 순회, ordinal 계산
    ord_counter: dict[str, int] = defaultdict(int)
    leaf_seq: list[dict] = []
    for el in root.iter():
        if el is root:
            continue
        kids = list(el)
        if kids:
            continue
        txt = el.text  # 원문 그대로, None이면 ''로 둔다
        tag = el.tag.split('}', 1)[1] if '}' in el.tag else el.tag
        ord_counter[tag] += 1
        leaf_seq.append({
            'el': el,
            'tag': tag,
            'text': txt if txt is not None else '',
            'ord': ord_counter[tag],
        })

    # 2. 문자열 속성 또는 제네릭 텍스트 leaf 중 값이 있는 것만 추출
    for lf in leaf_seq:
        if not lf['text'].strip():  # 빈 텍스트는 최종 결과에서 제외 (ordinal은 유지)
            continue
        tag = lf['tag']
        # 태그명으로 문자열 속성 판별
        if tag in _STRING_TAGS or tag in _VT_STRING:
            pass  # 삭제 후보
        else:
            # 그 외 leaf (통계/boolean 등)는 삭제 후보에서 제외
            continue
        mid = f'app|{tag}|{lf["ord"]}'
        out.append({
            'id': mid,
            'label': tag,
            'value': lf['text'],  # 원문 그대로 (strip 하지 않음)
            'source': 'docProps/app.xml',
            'action': 'delete',
            'element': lf['el'],
        })
    return out



def inspect_metadata(package: dict) -> tuple[list[dict], list[str]]:
    """Discover all metadata leaves from a read package.

    Returns (meta_list, uninspected_meta_labels).
    meta_list items: {id,label,value,source,action,element}
    """
    meta: list[dict] = []
    uninspected: list[str] = []

    core_items = _core_meta(package.get('core'))
    meta.extend(core_items)

    custom_items = _custom_meta(package.get('custom'))
    if package.get('custom') is not None and not custom_items:
        uninspected.append('사용자 지정 속성 (읽기 어려운 구조)')
    meta.extend(custom_items)

    app_items = _app_meta(package.get('app'))
    meta.extend(app_items)

    return meta, uninspected


# ---------------------------------------------------------------------------
# Unit (paragraph) extraction
# ---------------------------------------------------------------------------

def _ancestor_is_nested_p(el: etree._Element, top_p: etree._Element,
                          body_root: etree._Element) -> bool:
    """True if el is inside a w:p that is a descendant of top_p but not top_p itself.

    This detects nesting within the same body (e.g. textbox paragraphs).
    """
    parent = el.getparent()
    while parent is not None:
        if parent is top_p:
            return False
        local = parent.tag.split('}', 1)[1] if '}' in parent.tag else parent.tag
        if local == 'p':
            return True
        parent = parent.getparent()
    return False


def _run_descendants_text(r: etree._Element, p_el: etree._Element) -> str:
    """Collect text from a w:r's direct children: w:t, w:tab, w:br/w:cr.

    Skips runs inside nested textbox paragraphs.
    """
    if _ancestor_is_nested_p(r, p_el, r.getroottree().getroot()):
        return ''
    chunks: list[str] = []
    for child in r:
        local = child.tag.split('}', 1)[1] if '}' in child.tag else child.tag
        if local == 't':
            chunks.append(child.text or '')
        elif local == 'tab':
            chunks.append('\t')
        elif local in ('br', 'cr'):
            chunks.append('\n')
    return ''.join(chunks)


def get_units(package: dict) -> list[dict]:
    """Extract paragraph units from document + header + footer XML parts.

    Returns list of:
        {id, text, page: None, locator: {part, p_index}}

    p_index: global order of w:p in the part (including tables), counting
             ALL paragraphs.  Nested textbox paragraphs are excluded from
             units but still consume a p_index number.
    id: stable string 'part#p_index'.
    """
    units: list[dict] = []
    xml = package['xml_parts']

    for part_name in ('word/document.xml',):
        if part_name not in xml:
            continue
        root = _safe_parse(xml[part_name])
        p_idx = 0
        # First pass: count all w:p to assign indices
        p_map: list[etree._Element] = []
        for p in root.iter(f'{{{W_NS}}}p'):
            p_map.append(p)
        # Second pass: build units, skipping nested textbox paragraphs
        for idx, p_el in enumerate(p_map):
            # Check if this p is inside a txbxContent ancestor
            in_textbox = False
            ancestor = p_el.getparent()
            while ancestor is not None:
                local = ancestor.tag.split('}', 1)[1] if '}' in ancestor.tag else ancestor.tag
                if local == 'txbxContent':
                    in_textbox = True
                    break
                ancestor = ancestor.getparent()
            if in_textbox:
                p_idx += 1
                continue
            # Collect text from runs whose closest w:p ancestor is THIS p
            text_parts: list[str] = []
            for r in p_el.iter(f'{{{W_NS}}}r'):
                # Only include runs whose nearest w:p ancestor is p_el
                parent = r.getparent()
                run_ok = False
                while parent is not None:
                    local = parent.tag.split('}', 1)[1] if '}' in parent.tag else parent.tag
                    if local == 'p':
                        if parent is p_el:
                            run_ok = True
                        break
                    parent = parent.getparent()
                if not run_ok:
                    continue
                text_parts.append(_run_descendants_text(r, p_el))
            text = ''.join(text_parts)
            uid = f'/word/document.xml#{idx}'
            units.append({
                'id': uid,
                'text': text,
                'page': None,
                'locator': {'part': part_name, 'p_index': idx},
            })
            p_idx += 1

    # Header/footer parts
    for fn in sorted(xml):
        if not (fn.startswith('word/header') or fn.startswith('word/footer')):
            continue
        if not fn.lower().endswith('.xml'):
            continue
        root = _safe_parse(xml[fn])
        p_map = list(root.iter(f'{{{W_NS}}}p'))
        for idx, p_el in enumerate(p_map):
            in_textbox = False
            ancestor = p_el.getparent()
            while ancestor is not None:
                local = ancestor.tag.split('}', 1)[1] if '}' in ancestor.tag else ancestor.tag
                if local == 'txbxContent':
                    in_textbox = True
                    break
                ancestor = ancestor.getparent()
            if in_textbox:
                continue
            text_parts: list[str] = []
            for r in p_el.iter(f'{{{W_NS}}}r'):
                parent = r.getparent()
                run_ok = False
                while parent is not None:
                    local = parent.tag.split('}', 1)[1] if '}' in parent.tag else parent.tag
                    if local == 'p':
                        if parent is p_el:
                            run_ok = True
                        break
                    parent = parent.getparent()
                if not run_ok:
                    continue
                text_parts.append(_run_descendants_text(r, p_el))
            text = ''.join(text_parts)
            uid = f'/{fn}#{idx}'
            units.append({
                'id': uid,
                'text': text,
                'page': None,
                'locator': {'part': fn, 'p_index': idx},
            })
    return units


# ---------------------------------------------------------------------------
# Uninspected feature report
# ---------------------------------------------------------------------------

def find_uninspected(package: dict) -> list[str]:
    """Scan all XML for features we cannot redact; return human labels."""
    seen: set[str] = set()
    for fn, data in package['xml_parts'].items():
        labels = _find_uninspected(data)
        for lab in labels:
            seen.add(f'{fn}: {lab}')
    return sorted(seen)
