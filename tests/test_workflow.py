import copy
import io
import json
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image

from studio.demo import create_demo
from studio.ocr import group_lines
from studio.pipeline import run
from studio.settings import crypt
from studio.storage import Project, TranslationMemory
from studio.translate import DEFAULT_CONFIG, Budget, FatalTranslationError, Translator
from studio.typeset import export_pdf, get_jobs, layout_issues, preview


class MockAPI(BaseHTTPRequestHandler):
    mode = 'ok'
    calls = []

    def log_message(self, *args): pass

    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).calls.append(body)
        if self.mode=='denied':self.send_response(401);self.end_headers();return
        data=json.loads(body['messages'][-1]['content'])
        translations=[]
        for line in data['lines']:
            text=line['text'].upper()
            value='我们有300年的时光，去记住这一切。' if '300' in text else '我不是你的敌人。'
            translations.append({'id':line['id'],'text':value})
        if self.mode=='duplicate':translations=[translations[0],translations[0]]
        if self.mode=='wrong_type':translations[0]['id']=True
        payload={'choices':[{'message':{'content':json.dumps({'translations':translations},ensure_ascii=False)}}],
                 'usage':{'prompt_tokens':100,'completion_tokens':30}}
        raw=json.dumps(payload).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),MockAPI)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.memory=TranslationMemory(self.root/'memory.sqlite')
        self.mockmemory=patch('studio.translate.TranslationMemory',return_value=self.memory);self.mockmemory.start();self.addCleanup(self.mockmemory.stop)
        MockAPI.mode='ok';MockAPI.calls=[]
        self.config=dict(DEFAULT_CONFIG,endpoint=f'http://127.0.0.1:{self.server.server_port}',workers=1,secondary_ocr=False,context='test')

    def translator(self):return Translator(self.config,'dummy-test-key',threading.Event(),Budget(20))

    def test_id_integrity_rejects_duplicate_and_boolean(self):
        groups=[{'id':i,'role':'dialogue','text':str(i),'zh':''} for i in range(2)]
        for mode in ['duplicate','wrong_type']:
            MockAPI.mode=mode
            with self.assertRaises(RuntimeError):self.translator().translate(groups)
            self.assertTrue(all(not g['zh'] for g in groups))

    def test_payload_cache_context_and_manual_preservation(self):
        groups=[{'id':1,'role':'dialogue','text':'I AM NOT YOUR ENEMY.','zh':''}]
        t=self.translator();t.translate(groups)
        payload=MockAPI.calls[-1]
        self.assertEqual(payload['thinking'],{'type':'disabled'});self.assertNotIn('tools',payload)
        self.assertEqual(len(payload['messages']),2);self.assertEqual(payload['response_format'],{'type':'json_object'})
        groups[0]['zh']='';t.translate(groups);self.assertEqual(len(MockAPI.calls),1)
        self.config['context']='different';groups[0]['zh']='';self.translator().translate(groups);self.assertEqual(len(MockAPI.calls),2)
        groups[0]['zh']='人工校译';self.translator().translate(groups);self.assertEqual(groups[0]['zh'],'人工校译');self.assertEqual(len(MockAPI.calls),2)

    def test_request_budget_and_credentials_stop(self):
        b=Budget(1);b.reserve()
        with self.assertRaises(FatalTranslationError):b.reserve()
        p=create_demo(self.root/'p');d=p.load_page(p.ordered()[0]['id'])
        for g in d['groups']:g['zh']=''
        p.save_page(d);MockAPI.mode='denied';stop=threading.Event()
        result=run(p,self.config,'dummy',stop)
        self.assertTrue(result['cancelled']);self.assertEqual(len(MockAPI.calls),1)
        self.assertEqual(p.load_page(d['id'])['status'],'error')
        self.assertTrue(p.load_page(d['id'])['ocr_complete'])

    def test_natural_order_pixel_dedupe_and_cbz_no_extraction(self):
        src=self.root/'src';src.mkdir()
        Image.new('RGB',(40,60),'red').save(src/'10.png');Image.new('RGB',(40,60),'blue').save(src/'2.png')
        p=Project(self.root/'project',create=True);self.assertEqual(p.import_paths([src]),(2,0))
        self.assertEqual([x['label'] for x in p.ordered()],['2.png','10.png'])
        self.assertEqual(p.import_paths([src]),(0,2))
        buf=io.BytesIO();Image.new('RGB',(40,60),'green').save(buf,'PNG')
        cbz=self.root/'pages.cbz'
        with zipfile.ZipFile(cbz,'w') as z:z.writestr('../../escape.png',buf.getvalue())
        self.assertEqual(p.import_paths([cbz]),(1,0));self.assertFalse((self.root/'escape.png').exists())
        pdf=self.root/'input.pdf'
        with fitz.open() as doc:
            page=doc.new_page(width=120,height=200);page.draw_rect(fitz.Rect(20,20,90,90),color=(0,0,0));doc.save(pdf)
        self.assertEqual(p.import_paths([pdf]),(1,0))

    def test_short_ocr_tokens_retained(self):
        lines=[{'box':[10,10+30*i,100,30+30*i],'text':v,'confidence':1} for i,v in enumerate(['I','NO','300'])]
        text=' '.join(g['text'] for g in group_lines(lines));self.assertIn('I',text);self.assertIn('NO',text);self.assertIn('300',text)

    def test_moved_layout_keeps_source_erase_boxes(self):
        p=create_demo(self.root/'p');d=p.load_page(p.ordered()[0]['id']);original=copy.deepcopy(d['groups'][0]['lines'])
        d['groups'][0]['layout_box']=[100.5,70.5,350.5,210.5]
        _,jobs=get_jobs(d);j=next(j for j in jobs if 1 in j['ids'])
        self.assertEqual(j['lines'],original);self.assertTrue(j['free_layout']);self.assertEqual(j['box'][0],100.5)
        _,rep=preview(d,max_edge=600);self.assertTrue(all(not r['needs_repair'] for r in rep))

    def test_export_embedded_text_and_blocks_bad_layout(self):
        p=create_demo(self.root/'p');target=self.root/'out.pdf';result=export_pdf(p,target)
        self.assertEqual(result['pages'],2)
        with fitz.open(target) as doc:
            self.assertEqual(len(doc),2);self.assertIn('不是',doc[0].get_text());self.assertTrue(doc.get_toc())
        before=target.read_bytes();d=p.load_page(p.ordered()[0]['id']);d['groups'][0]['zh']='极长译文'*500;p.save_page(d)
        with self.assertRaises(RuntimeError):export_pdf(p,target)
        self.assertEqual(target.read_bytes(),before);self.assertFalse(target.with_name('out.partial.pdf').exists())
        report=json.loads(target.with_suffix('.检查报告.json').read_text(encoding='utf-8'));self.assertTrue(report['errors'])

    def test_unprocessed_blank_page_not_silently_exported(self):
        p=Project(self.root/'p',create=True);p.add_image(Image.new('RGB',(200,300),'white'),'1','1')
        with self.assertRaises(RuntimeError):export_pdf(p,self.root/'out.pdf')

    def test_real_ocr_mock_translation_export_and_resume(self):
        p=create_demo(self.root/'p');first=p.ordered()[0]['id'];d=p.load_page(first)
        d.update(groups=[],ocr_complete=False,secondary_complete=False,status='imported');p.save_page(d)
        result=run(p,self.config,'dummy',threading.Event(),page_ids=[first])
        self.assertEqual(result['done'],1);self.assertEqual(result['errors'],0)
        d=p.load_page(first);self.assertEqual(len(d['groups']),2)
        self.assertTrue(any('300' in g['zh'] for g in d['groups']));self.assertTrue(any('不是' in g['zh'] for g in d['groups']))
        target=self.root/'book.pdf';self.assertEqual(export_pdf(p,target)['pages'],2)
        calls=len(MockAPI.calls)
        with patch('studio.pipeline.detect',side_effect=AssertionError('resume repeated OCR')):
            run(p,self.config,'dummy',threading.Event())
        self.assertEqual(len(MockAPI.calls),calls)

    def test_windows_key_encryption(self):
        cipher=crypt('dummy-secret');self.assertNotIn('dummy-secret',cipher);self.assertEqual(crypt(cipher,True),'dummy-secret')


if __name__=='__main__':unittest.main(verbosity=2)
