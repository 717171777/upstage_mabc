"""Run with `python -m verification.audit_local_detection`; no network or AI.

Measures (type, value) occurrence counts across body, tables, headers and
footers. Account bank prefixes in the answer key are excluded: the detector
masks account numbers and retains bank names. All other values match exactly.
This is not a coordinate-level or end-to-end AI benchmark.
"""
import json
import re
import sys
from contextlib import redirect_stdout
from collections import Counter
from pathlib import Path

with redirect_stdout(sys.stderr):
    from backend.engine import inspect_document
    from backend.local_detection import detect_local


def audit():
    root = Path(__file__).resolve().parents[1] / 'fixtures/eval_v0'
    truth = [json.loads(line) for line in (root / 'ground_truth.jsonl').read_text().splitlines()]
    expected, found, hits = Counter(), Counter(), Counter()
    documents = []
    for line in (root / 'documents.jsonl').read_text().splitlines():
        document = json.loads(line)
        path = root / document['file']
        try:
            candidates = detect_local(path, inspect_document(path))
        except (ValueError, RuntimeError) as error:
            documents.append({'document': document['doc_id'], 'blocked': str(error)})
            continue
        wanted = Counter((row['type_id'], re.sub(r'^[가-힣]+\s+', '', row['raw_value'])
                          if row['type_id'] == 'account' else row['raw_value']) for row in truth
                         if row['doc_id'] == document['doc_id'])
        got = Counter((candidate['type'], candidate['value']) for candidate in candidates)
        matched = wanted & got
        for counts, target in ((wanted, expected), (got, found), (matched, hits)):
            for (kind, _), number in counts.items():
                target[kind] += number
        documents.append({'document': document['doc_id'], 'expected': sum(wanted.values()),
                          'detected': sum(got.values()), 'matched': sum(matched.values()),
                          'missed': sum((wanted - got).values()), 'extra': sum((got - wanted).values())})
    return {
        'scope': 'Local detection only; type/value occurrence counts across all text surfaces; account bank prefixes excluded; no external AI',
        'totals': {'expected': sum(expected.values()), 'detected': sum(found.values()), 'matched': sum(hits.values())},
        'types': {kind: {'expected': expected[kind], 'detected': found[kind], 'matched': hits[kind]}
                  for kind in sorted(set(expected) | set(found))},
        'documents': documents,
    }


if __name__ == '__main__':
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
