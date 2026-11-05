"""Offline parser/render/security contracts. API stubs are NOT live evidence."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw
from app.core.config import get_settings
from app.services.document_pipeline import extract, PipelineFailure
from app.services.document_vision import render_page, reserve_call, extract_page
from tests.test_document_rag import pdf_bytes, word_bytes


def scan_pdf(pages=1):
    images=[]
    for index in range(pages):
        image=Image.new('RGB',(800,1000),'white')
        ImageDraw.Draw(image).text((40,80),f'Question {index+1}: Choose a color. A Red B Blue C Green D Black',fill='black')
        images.append(image)
    output=io.BytesIO()
    images[0].save(output,format='PDF',save_all=True,append_images=images[1:])
    for image in images:
        image.close()
    return output.getvalue()


class MultimodalTests(unittest.TestCase):
    def test_cli_preserves_vision_failure_identity_and_page(self):
        import os,sys,subprocess,json,hashlib
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=scan_pdf();(root/'scan.pdf').write_bytes(data)
            env={**os.environ,'OPENAI_API_KEY':''}
            run=subprocess.run([sys.executable,'-m','app.services.document_pipeline',
                str(root/'scan.pdf'),str(root/'result.json'),'.pdf','unused',hashlib.sha256(data).hexdigest()],
                cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,timeout=30)
            self.assertEqual(run.returncode,0)
            result=json.loads((root/'result.json').read_text())
            self.assertEqual(result['error'],'DOCUMENT_VISION_KEY_MISSING')
            self.assertEqual(result['page_number'],1)

    def test_native_pdf_docx_no_vision_call(self):
        with patch('app.services.document_vision.extract_page',side_effect=AssertionError('unexpected paid API')):
            self.assertIn('PostgreSQL',extract(pdf_bytes(),'.pdf')[0][0])
            self.assertIn('PostgreSQL',extract(word_bytes(),'.docx')[0][0])

    def test_real_render_and_pixels(self):
        image=Image.open(io.BytesIO(render_page(scan_pdf(),1)))
        self.assertLessEqual(image.width*image.height,4_000_000)
        self.assertEqual(image.format,'JPEG')

    def test_docx_image_transparency_and_multiframe_coverage(self):
        from app.services.document_vision import normalize_image
        out=io.BytesIO();Image.new('RGBA',(20,20),(0,0,0,0)).save(out,format='PNG')
        with Image.open(io.BytesIO(normalize_image(out.getvalue()))) as actual:
            self.assertTrue(all(v>245 for v in actual.getpixel((0,0))))
        out=io.BytesIO();a=Image.new('RGB',(20,20),'red');b=Image.new('RGB',(20,20),'blue')
        a.save(out,format='GIF',save_all=True,append_images=[b]);a.close();b.close()
        with self.assertRaisesRegex(PipelineFailure,'DOCX_IMAGES_UNSUPPORTED'):normalize_image(out.getvalue())

    def test_16_and_26_scan_pages_contract(self):
        for pages in (16,26):
            with self.subTest(pages=pages),patch('app.services.document_vision.extract_page',return_value={
                    'text':'Question 1: A Red B Blue C Green D Black','visual_description':'','warning':''}) as api:
                units=extract(scan_pdf(pages),'.pdf')
            self.assertEqual(api.call_count,pages)
            self.assertEqual([loc['page'] for _,loc in units],list(range(1,pages+1)))
            self.assertTrue(all(loc['visual'] for _,loc in units))

    def test_mixed_pdf_and_visual_description(self):
        from pypdf import PdfReader, PdfWriter
        writer=PdfWriter()
        for content in (pdf_bytes(),scan_pdf()):
            writer.add_page(PdfReader(io.BytesIO(content)).pages[0])
        output=io.BytesIO();writer.write(output)
        with patch('app.services.document_vision.extract_page',return_value={
                'text':'','visual_description':'Red bar 10, blue bar 20','warning':''}):
            units=extract(output.getvalue(),'.pdf')
        self.assertEqual(units[0][1]['extraction_method'],'native')
        self.assertIn('Red bar 10',units[1][0])
        self.assertEqual(units[1][1]['page'],2)

    def test_page_error_has_locator(self):
        with patch('app.services.document_vision.extract_page',side_effect=PipelineFailure('DOCUMENT_VISION_QUOTA')):
            with self.assertRaises(PipelineFailure) as caught:
                extract(scan_pdf(),'.pdf')
        self.assertEqual(caught.exception.page_number,1)

    def test_missing_key_no_network(self):
        settings=get_settings().model_copy(update={'openai_api_key':__import__('pydantic').SecretStr('')})
        with patch('app.services.document_vision.get_settings',return_value=settings):
            with self.assertRaisesRegex(PipelineFailure,'DOCUMENT_VISION_KEY_MISSING'):
                extract_page(b'not sent')

    def test_daily_cap_is_durable_and_keeps_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=SimpleNamespace(document_vision_ledger=str(Path(folder)/'cap.db'),document_vision_daily_call_cap=1)
            with patch('app.services.document_vision.get_settings',return_value=settings):
                reserve_call()
                with self.assertRaisesRegex(PipelineFailure,'DOCUMENT_VISION_CALL_CAP'):
                    reserve_call()

    def test_render_invalid_page_and_malformed(self):
        for content,number in ((scan_pdf(),0),(b'%PDF-broken',1)):
            with self.assertRaises(PipelineFailure):
                render_page(content,number)

    def test_embedded_docx_has_region_provenance_and_real_original_render(self):
        from docx import Document
        doc=Document();doc.add_paragraph('Text with image')
        image=io.BytesIO();Image.new('RGB',(20,20),'red').save(image,format='PNG');image.seek(0)
        doc.add_picture(image);output=io.BytesIO();doc.save(output)
        with patch('app.services.document_vision.extract_page',return_value={
                'text':'','visual_description':'Red square','warning':''}) as api:
            units=extract(output.getvalue(),'.docx')
        self.assertEqual(api.call_count,1)
        visual=[(text,loc) for text,loc in units if loc.get('embedded_image_id')]
        self.assertEqual(len(visual),1)
        self.assertIn('Red square',visual[0][0]);self.assertEqual(visual[0][1]['coverage'],'complete')
        from app.services.document_vision import render_docx_region
        raw=render_docx_region(output.getvalue(),visual[0][1]['embedded_image_id'])
        self.assertTrue(raw.startswith(b'\xff\xd8'))

    def test_header_docx_image_is_explicit_incomplete_failure(self):
        from docx import Document
        doc=Document();doc.add_paragraph('Body')
        image=io.BytesIO();Image.new('RGB',(20,20),'red').save(image,format='PNG');image.seek(0)
        doc.sections[0].header.paragraphs[0].add_run().add_picture(image)
        output=io.BytesIO();doc.save(output)
        with patch('app.services.document_vision.extract_page',side_effect=AssertionError('No API')):
            with self.assertRaisesRegex(PipelineFailure,'DOCX_IMAGES_UNSUPPORTED'):
                extract(output.getvalue(),'.docx')

    def test_unsupported_docx_shape_cannot_publish_complete_ready(self):
        from docx import Document
        from docx.oxml import OxmlElement
        doc=Document();p=doc.add_paragraph('Body');p._p.append(OxmlElement('w:pict'))
        out=io.BytesIO();doc.save(out)
        with self.assertRaisesRegex(PipelineFailure,'DOCX_IMAGES_UNSUPPORTED'):
            extract(out.getvalue(),'.docx')

    def test_docx_header_text_retains_part_locator_and_nested_tables_fail_explicitly(self):
        from docx import Document
        doc=Document();doc.add_paragraph('Body');doc.sections[0].header.paragraphs[0].text='Header control value 314'
        out=io.BytesIO();doc.save(out)
        units=extract(out.getvalue(),'.docx')
        self.assertTrue(any('314' in text and locator.get('part')=='header' for text,locator in units))
        doc.add_table(rows=1,cols=1).cell(0,0).add_table(rows=1,cols=1).cell(0,0).text='Nested data'
        out=io.BytesIO();doc.save(out)
        with self.assertRaisesRegex(PipelineFailure,'DOCX_LAYOUT_UNSUPPORTED'):extract(out.getvalue(),'.docx')

    def test_scan_page_cap_before_network(self):
        settings=get_settings().model_copy(update={'document_vision_max_pages':1})
        with patch('app.core.config.get_settings',return_value=settings):
            with self.assertRaisesRegex(PipelineFailure,'DOCUMENT_PAGE_LIMIT'):
                extract(scan_pdf(2),'.pdf')

    def test_password_pdf_and_blank_not_ready(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(400,400);writer.encrypt('private')
        output=io.BytesIO();writer.write(output)
        with self.assertRaisesRegex(PipelineFailure,'DOCUMENT_PASSWORD_PROTECTED'):
            extract(output.getvalue(),'.pdf')
        with self.assertRaisesRegex(PipelineFailure,'PDF_NO_TEXT'):
            extract(pdf_bytes(blank=True),'.pdf')

    def test_real_sdk_image_contract_and_incomplete_response(self):
        import httpx
        import openai
        from pydantic import SecretStr
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            settings=get_settings().model_copy(update={'document_vision_ledger':str(Path(folder)/'cap.db'),
                'openai_api_key':SecretStr('offline-test-only')})
            seen=[]
            def reply(request):
                import json
                seen.append(json.loads(request.content))
                return httpx.Response(200,json={'id':'test','object':'response','created_at':0,
                    'status':'incomplete','model':'gpt-6-luna','output':[],
                    'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}})
            actual=openai.OpenAI
            def factory(**kwargs):
                return actual(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(reply)))
            with patch('app.services.document_vision.get_settings',return_value=settings),patch('openai.OpenAI',side_effect=factory):
                with self.assertRaisesRegex(PipelineFailure,'DOCUMENT_VISION_INCOMPLETE'):
                    extract_page(b'image bytes not transmitted to network')
            self.assertEqual(len(seen),1)
            self.assertEqual(seen[0]['input'][1]['content'][0]['type'],'input_image')
            self.assertFalse(seen[0]['store'])

    def test_real_sdk_error_classification_no_retries(self):
        import httpx, openai
        from pydantic import SecretStr
        for status,code,expected in ((401,'invalid_api_key','KEY_INVALID'),
                (429,'insufficient_quota','QUOTA'),(429,'rate_limit_exceeded','RATE_LIMIT'),
                (400,'invalid_image','FAILED')):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as folder:
                settings=get_settings().model_copy(update={'document_vision_ledger':str(Path(folder)/'cap.db'),
                    'openai_api_key':SecretStr('offline-test-only')})
                calls=[]
                def reply(request):
                    calls.append(1)
                    return httpx.Response(status,json={'error':{'message':'test','type':code,'code':code}})
                actual=openai.OpenAI
                def factory(**kwargs):
                    return actual(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(reply)))
                with patch('app.services.document_vision.get_settings',return_value=settings),patch('openai.OpenAI',side_effect=factory):
                    with self.assertRaisesRegex(PipelineFailure,'DOCUMENT_VISION_'+expected):
                        extract_page(b'offline')
                self.assertEqual(len(calls),1)
