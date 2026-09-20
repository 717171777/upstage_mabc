import hashlib
import json
import re
from pathlib import Path

from . import docx_engine, pdf_engine

TYPES = (
    "name", "phone", "email", "address", "dob",
    "resident_id", "foreign_id", "passport", "driver_license",
    "account", "card", "management_id",
)

DOCUMENT_TYPES = (
    "report_minutes", "contract_agreement", "transaction_settlement",
    "personnel_roster", "case_record", "other",
)

_ENGINE_MAP = {
    ".pdf": pdf_engine,
    ".docx": docx_engine,
}


def inspect_document(path):
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in _ENGINE_MAP:
        raise ValueError("지원하지 않는 PDF/DOCX 파일 형식입니다")
    return _ENGINE_MAP[suffix].inspect(p)


def render_document(source, destination, inspection, candidates, metadata_actions):
    source = Path(source)
    destination = Path(destination)
    fmt = inspection["format"]
    if fmt == "pdf":
        engine = pdf_engine
    elif fmt == "docx":
        engine = docx_engine
    else:
        raise ValueError("지원하지 않는 문서 형식입니다")
    return engine.render(source, destination, inspection, candidates, metadata_actions)


def candidate_id(type_id, unit_id, start, end):
    raw = json.dumps([type_id, unit_id, start, end], separators=(",", ":"), sort_keys=True)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return "c_" + h[:24]


def locate_value(inspection, value):
    if not isinstance(value, str) or not value:
        return []
    results = []
    for unit in inspection.get("units", []):
        text = unit.get("text", "")
        start = 0
        while True:
            idx = text.find(value, start)
            if idx == -1:
                break
            results.append({
                "unitId": unit["id"],
                "start": idx,
                "end": idx + len(value),
                "page": unit.get("page"),
            })
            start = idx + 1
    return results


def rule_candidates(inspection):
    units = inspection.get("units", [])

    resident_re = re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)")
    foreign_re = re.compile(r"(?<!\d)\d{6}[- ]?[5-8]\d{6}(?!\d)")
    phone_re = re.compile(r"(?<![\d\-.])(?:01[016789]|0\d{1,2})[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
    email_re = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")

    candidates = []

    for unit in units:
        uid = unit["id"]
        text = unit.get("text", "")
        page = unit.get("page")

        matches = []

        for m in resident_re.finditer(text):
            matches.append((m.start(), m.end(), "resident_id", m.group()))
        for m in foreign_re.finditer(text):
            matches.append((m.start(), m.end(), "foreign_id", m.group()))
        for m in phone_re.finditer(text):
            matches.append((m.start(), m.end(), "phone", m.group()))
        for m in email_re.finditer(text):
            matches.append((m.start(), m.end(), "email", m.group()))

        matches.sort(key=lambda x: x[0])

        kept = []
        for start, end, ctype, value in matches:
            overlapped = False
            for k in kept:
                if k["start"] < end and start < k["end"]:
                    if ctype not in k["alternativeTypes"]:
                        k["alternativeTypes"].append(ctype)
                        k["reason"] = "규칙으로 찾은 후보입니다. 직접 확인해 주세요. 다른 유형과 중복될 수 있습니다."
                    overlapped = True
                    break
            if not overlapped:
                cid = candidate_id(ctype, uid, start, end)
                kept.append({
                    "id": cid,
                    "type": ctype,
                    "value": value,
                    "unitId": uid,
                    "start": start,
                    "end": end,
                    "page": page,
                    "method": "full",
                    "mask": [],
                    "confirmed": False,
                    "locationResolved": True,
                    "source": "rule",
                    "reason": "규칙으로 찾은 후보입니다. 직접 확인해 주세요.",
                    "alternativeTypes": [],
                })

        candidates.extend(kept)

    return candidates
