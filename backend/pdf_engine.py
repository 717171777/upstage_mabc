from pathlib import Path
import hashlib
import pymupdf as fitz
from .pdf_io import inspect, metadata_items, apply_metadata
from .docx_engine import _validate_candidates


def render(
    source: Path,
    destination: Path,
    inspection: dict,
    candidates: list,
    metadata_actions: dict,
) -> dict:
    source = Path(source)
    destination = Path(destination)

    # 1. 동일한 resolved 경로 거부
    if source.resolve() == destination.resolve():
        raise ValueError("소스와 목적지가 동일합니다")

    # 실제 소스 재검사 후 supplied inspection과 정확히 일치해야 함
    actual = inspect(source)
    if actual != inspection:
        raise ValueError("검사 결과가 일치하지 않습니다")

    # 형식 확인
    if source.suffix.lower() != ".pdf":
        raise ValueError("PDF 형식이 아닙니다")

    # 소스 SHA256 (전체 파일) — 소스 해시 가드
    src_bytes = source.read_bytes()
    src_sha = hashlib.sha256(src_bytes).hexdigest()

    # 후보 검증 (문서화 된 계약: ID->후보, _mask_merged 포함)
    cands = _validate_candidates(inspection, candidates)

    # 2. 선택 집합: (unitId, absoluteCharacterIndex) -> method
    # inspection['units']는 LIST이므로 units_by_id로 변환
    units_by_id = {u['id']: u for u in inspection['units']}
    selected = {}
    for cd in cands.values():
        uid = cd['unitId']
        st = cd['start']
        en = cd['end']
        method = cd.get("method", "keep")
        if method in ("full", "delete"):
            for i in range(st, en):
                selected[(uid, i)] = method
        elif method == "partial":
            for seg in cd.get("_mask_merged", []):
                # partial 구간은 candidate.start 기준 상대 인덱스
                for i in range(st + seg[0], st + seg[1]):
                    selected[(uid, i)] = method
        # keep: 아무것도 추가하지 않음

    # 3. 페이지별 가리개 상자 준비 (rawdict 비회전 점)
    # key: 1-based page -> list of (bbox, fill)
    page_ops = {}
    for (uid, ci), method in selected.items():
        unit = units_by_id[uid]
        chars = unit["chars"]
        box = chars[ci]["bbox"]  # [x0,y0,x1,y1]
        x0, y0, x1, y1 = box
        w = x1 - x0
        h = y1 - y0
        if w > 0.02:
            x0 += 0.01
            x1 -= 0.01
        if h > 0.02:
            y0 += 0.01
            y1 -= 0.01
        if x1 - x0 <= 0:
            x0, x1 = box[0], box[2]
        if y1 - y0 <= 0:
            y0, y1 = box[1], box[3]
        fill = (0, 0, 0) if method in ("full", "partial") else (1, 1, 1)
        pg = unit["page"]
        page_ops.setdefault(pg, []).append(([x0, y0, x1, y1], fill))

    # 소스 열기
    doc = fitz.open(source)

    try:
        for pg_idx, ops in page_ops.items():
            page = doc[pg_idx - 1]
            for b, color in ops:
                page.add_redact_annot(b, fill=color, cross_out=False)

        for page in doc:
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_PIXELS,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )

        apply_metadata(doc, metadata_actions)

        doc.save(
            destination,
            incremental=False,
            garbage=4,
            deflate=True,
            clean=True,
        )
    finally:
        doc.close()

    # 4. 저장본 재개방 및 독립 검증
    # 모든 생성/검증 코드를 try/except로 감싸 실패 시 목적지 삭제 후 ValueError
    try:
        ddoc = fitz.open(destination)
        try:
            fitz.TOOLS.set_small_glyph_heights(True)

            with fitz.open(source) as sdoc:
                checks = []

                # 페이지 수
                same_page_count = ddoc.page_count == sdoc.page_count
                checks.append({"name": "페이지 수 동일", "passed": same_page_count})
                if not same_page_count:
                    raise _fail(dst=destination)

                # 각 페이지 mediabox, cropbox, rotation 비교
                geom_ok = True
                for i in range(sdoc.page_count):
                    sp = sdoc[i]
                    dp = ddoc[i]
                    if (sp.mediabox != dp.mediabox or
                        sp.cropbox != dp.cropbox or
                        sp.rotation != dp.rotation):
                        geom_ok = False
                        break
                checks.append({"name": "페이지 기하 동일", "passed": geom_ok})
                if not geom_ok:
                    raise _fail(dst=destination)

                # 출력 비공백 글리프 수집 (rawdict)
                # 불변 all-output-glyphs 사전 (선택 글리프 leak 확인용)
                # 페이지별 mutable remaining 리스트 (미선택 매칭/소비용)
                page_glyphs_all = {}
                page_glyphs_remaining = {}
                for pno in range(1, ddoc.page_count + 1):
                    page = ddoc[pno - 1]
                    d = page.get_text("rawdict")
                    arr = []
                    for blk in d.get("blocks", []):
                        if blk.get("type", 0) != 0:
                            continue
                        for ln in blk.get("lines", []):
                            for sp in ln.get("spans", []):
                                for ch in sp.get("chars", []):
                                    c = ch["c"]
                                    if not c.isspace():
                                        arr.append({"c": c, "bbox": ch["bbox"]})
                    page_glyphs_all[pno] = arr
                    page_glyphs_remaining[pno] = list(arr)

                # 원본 비공백 글리프별 검증
                content_ok = True
                for uid, unit in units_by_id.items():
                    pno = unit["page"]
                    chars = unit["chars"]
                    for ci, ch in enumerate(chars):
                        c = ch["c"]
                        if c.isspace():
                            continue
                        ob = ch["bbox"]
                        o_cx = (ob[0] + ob[2]) / 2.0
                        o_cy = (ob[1] + ob[3]) / 2.0
                        sel_method = selected.get((uid, ci))
                        if sel_method is not None:
                            # 선택된 글리프: 불변 all-output-glyphs에서 leak 확인
                            leak = False
                            for g in page_glyphs_all.get(pno, []):
                                gb = g["bbox"]
                                gx = (gb[0] + gb[2]) / 2.0
                                gy = (gb[1] + gb[3]) / 2.0
                                if abs(gx - o_cx) <= 0.35 and abs(gy - o_cy) <= 0.35:
                                    leak = True
                                    break
                            if leak:
                                content_ok = False
                                break
                        else:
                            # 선택 안 된 글리프: remaining 리스트에서 소비
                            found = -1
                            for idx, g in enumerate(page_glyphs_remaining.get(pno, [])):
                                if g["c"] != c:
                                    continue
                                gb = g["bbox"]
                                if (
                                    abs(gb[0] - ob[0]) <= 0.35
                                    and abs(gb[1] - ob[1]) <= 0.35
                                    and abs(gb[2] - ob[2]) <= 0.35
                                    and abs(gb[3] - ob[3]) <= 0.35
                                ):
                                    found = idx
                                    break
                            if found == -1:
                                content_ok = False
                                break
                            page_glyphs_remaining[pno].pop(found)
                    if not content_ok:
                        break

                # 미소비 출력 비공백 글리프 존재 여부 (remaining 리스트 기준)
                leftover_ok = True
                for pno in range(1, ddoc.page_count + 1):
                    if page_glyphs_remaining.get(pno, []):
                        leftover_ok = False
                        break
                checks.append({"name": "선택적 글자 제거", "passed": content_ok and leftover_ok})
                if not (content_ok and leftover_ok):
                    raise _fail(dst=destination)

                # 5. 메타데이터 검증: expected == actual EXACT 일치
                expected = {
                    m['id']: m['value']
                    for m in inspection['metadata']
                    if metadata_actions.get(m['id'], 'delete') == 'keep'
                }
                actual_meta = {m['id']: m['value'] for m in metadata_items(ddoc)}
                meta_ok = (expected == actual_meta)
                checks.append({"name": "메타데이터", "passed": meta_ok})
                if not meta_ok:
                    raise _fail(dst=destination)

                # 소스 SHA256 불변 가드
                src_now = source.read_bytes()
                src_now_sha = hashlib.sha256(src_now).hexdigest()
                src_unchanged = src_now_sha == src_sha
                checks.append({"name": "소스 불변", "passed": src_unchanged})
                if not src_unchanged:
                    raise _fail(dst=destination)

                # 6. 세이브 무결성: startxref 1개, /Prev 없음
                save_ok = False
                try:
                    saved_bytes = destination.read_bytes()
                    count = saved_bytes.count(b'startxref')
                    trailer = ddoc.pdf_trailer()
                    no_prev = '/Prev' not in trailer
                    save_ok = count == 1 and no_prev
                except Exception:
                    save_ok = False
                checks.append({"name": "완전 저장", "passed": save_ok})
                if not save_ok:
                    raise _fail(dst=destination)

                # 목적지 SHA256
                dst_sha = hashlib.sha256(destination.read_bytes()).hexdigest()

                passed = all(check['passed'] for check in checks)
                return {
                    "passed": passed,
                    "checks": checks,
                    "sha256": dst_sha,
                }
        finally:
            ddoc.close()
    except Exception:
        if destination.exists():
            destination.unlink()
        raise ValueError("PDF 처리 결과가 유효하지 않습니다")


def _fail(dst: Path) -> None:
    if dst.exists():
        dst.unlink()
    raise ValueError("PDF 처리 결과가 유효하지 않습니다")
