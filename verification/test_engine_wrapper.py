"""Independent review of shared engine contracts, no external calls."""
from pathlib import Path
import pytest
from backend import engine

ROOT=Path(__file__).resolve().parents[1]

def test_inspection_preserves_pdf_geometry():
    p=ROOT/'fixtures/eval_v0/docs/report_minutes-01.pdf'
    assert engine.inspect_document(p)==engine.pdf_engine.inspect(p)

def test_render_returns_report(monkeypatch):
    report={'passed':True,'checks':[],'sha256':'synthetic'}
    monkeypatch.setattr(engine.pdf_engine,'render',lambda *args:report)
    assert engine.render_document('a.pdf','b.pdf',{'format':'pdf'},[],{}) is report

@pytest.mark.parametrize('value',[None,'',1,{},['a']])
def test_invalid_lookup_value(value):
    assert engine.locate_value({'units':[{'id':'one','text':'banana','page':None}]},value)==[]

def test_identical_values_keep_distinct_locations():
    data={'units':[{'id':'first','text':'010-1234-5678 010-1234-5678','page':None},
                   {'id':'second','text':'010-1234-5678','page':None}]}
    found=engine.rule_candidates(data)
    assert len(found)==3 and len({c['id'] for c in found})==3
    assert {c['unitId'] for c in found}=={'first','second'}
    assert all(c['method']=='full' and not c['confirmed'] for c in found)

def test_email_excludes_label_and_punctuation():
    data={'units':[{'id':'one','text':'이메일:alpha@example.com, 담당자','page':1}]}
    found=[c for c in engine.rule_candidates(data) if c['type']=='email']
    assert len(found)==1 and found[0]['value']=='alpha@example.com'

def test_foreign_identifier_second_part():
    data={'units':[{'id':'one','text':'외국인등록번호 990101-5123456','page':1}]}
    found=engine.rule_candidates(data)
    assert len(found)==1 and found[0]['type']=='foreign_id' and found[0]['value']=='990101-5123456'
