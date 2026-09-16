"""Saved synthetic DOCX character counts, source removal and run styles."""
import hashlib
import zipfile
from pathlib import Path
from lxml import etree
import pytest
from backend.docx_engine import inspect, render

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
FIXTURE=Path(__file__).parents[1]/'fixtures/eval_v0/docs/docx-report_minutes-01.docx'

@pytest.mark.parametrize('method,mask,expected',[
 ('full',[], '████████'),
 ('partial',[[1,4],[6,8]],'김███ab██'),
 ('delete',[],''),
 ('keep',[],'김가람😀ab12'),
])
def test_saved_length_and_style(tmp_path,method,mask,expected):
 source=tmp_path/'source.docx';dest=tmp_path/'copy.docx'
 with zipfile.ZipFile(FIXTURE) as z: members={name:z.read(name) for name in z.namelist()}
 root=etree.fromstring(members['word/document.xml']);p=next(root.iter('{'+W+'}p'))
 for child in list(p):p.remove(child)
 for text,bold in [('김가',True),('람😀ab12',False)]:
  r=etree.SubElement(p,'{'+W+'}r');pr=etree.SubElement(r,'{'+W+'}rPr')
  if bold:etree.SubElement(pr,'{'+W+'}b')
  etree.SubElement(r,'{'+W+'}t').text=text
 members['word/document.xml']=etree.tostring(root)
 with zipfile.ZipFile(source,'w') as z:
  for name,data in members.items():z.writestr(name,data)
 before=hashlib.sha256(source.read_bytes()).digest();original=inspect(source);u=next(u for u in original['units'] if u['text']=='김가람😀ab12')
 c=dict(id='synthetic',type='name',value=u['text'],unitId=u['id'],start=0,end=8,method=method,mask=mask,confirmed=True,locationResolved=True)
 report=render(source,dest,original,[c],{})
 assert report['passed']
 assert next(v['text'] for v in inspect(dest)['units'] if v['id']==u['id'])==expected
 assert hashlib.sha256(source.read_bytes()).digest()==before
 with zipfile.ZipFile(dest) as z:
  result=etree.fromstring(z.read('word/document.xml'));p=next(result.iter('{'+W+'}p'));runs=list(p.iter('{'+W+'}r'))
  assert runs[0].find('{'+W+'}rPr/{'+W+'}b') is not None
  if method in ('full','partial'):
   texts=[''.join(r.itertext()) for r in runs]
   assert list(map(len,texts))==[2,6]
   assert '김가람😀ab12' not in ''.join(texts)
