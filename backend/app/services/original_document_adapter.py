"""Bounded original-byte adapters. Never read a search index or call a model.

DOCX exposes OOXML body blocks, not invented pages or rendered layout.
PDF/image interpretation requires an explicitly enabled visual model route.
"""
import hashlib
import io
import json
import zipfile
from xml.etree import ElementTree as ET
from app.core.learning_documents import MAX_DOCUMENT_BYTES

REVISION = 'original-units-v1'
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
MAX_UNITS = 10000


def docx_body(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if (len(entries)>1000 or sum(e.file_size for e in entries)>64*1024*1024
                or any(e.flag_bits & 1 or e.file_size>8*1024*1024 for e in entries)
                or len({e.filename for e in entries})!=len(entries)):
            raise ValueError('OOXML archive limit')
        if any('vbaproject' in e.filename.lower() or e.filename.lower().startswith(('word/embeddings/','word/activex/')) for e in entries):
            raise ValueError('Active OOXML unsupported')
        for e in entries:
            if e.filename.endswith(('.xml','.rels')):
                xml=archive.read(e)
                xml.decode('utf-8-sig')  # Deliberately reject unsupported XML encodings.
                if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():raise ValueError('Unsafe XML')
        xml=archive.read('word/document.xml')
    root=ET.fromstring(xml)
    if root.tag!=W+'document':raise ValueError('Invalid document root')
    body=root.find(W+'body')
    if body is None:raise ValueError('Missing document body')
    # Keep all body blocks, including unsupported ones, so none disappear from coverage.
    return list(body), {'fidelity':'original_ooxml_structure',
        'limitations':['body_blocks_only','no_stable_page_numbers','not_rendered_layout',
            'headers_footers_comments_not_in_body','fields_not_evaluated','external_links_never_fetched']}


def inventory(data, mime):
    if not data or len(data)>MAX_DOCUMENT_BYTES:raise ValueError('Original size limit')
    if mime=='application/pdf':
        from pypdf import PdfReader
        pdf=PdfReader(io.BytesIO(data),strict=True)
        if pdf.is_encrypted:raise ValueError('Encrypted PDF')
        count=len(pdf.pages)
        embedded=bool(pdf.trailer['/Root'].get('/Names',{}).get('/EmbeddedFiles')) if isinstance(pdf.trailer['/Root'].get('/Names',{}),dict) else bool(pdf.trailer['/Root'].get('/Names'))
        kinds=['page']*count
        fidelity={'fidelity':'physical_pages','limitations':['requires_visual_capability','rendering_is_not_lossless','embedded_files_and_dynamic_content_not_interpreted'],
            'whole_document_supported':not embedded}
    elif mime==DOCX:
        blocks,fidelity=docx_body(data)
        fidelity['whole_document_supported']=False  # Body-only is not a full DOCX survey.
        kinds=[b.tag.rsplit('}',1)[-1] for b in blocks]
        count=len(kinds)
    elif mime in {'image/png','image/jpeg'}:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        count=1;kinds=['image']
        fidelity={'fidelity':'original_image','limitations':['requires_visual_capability','normalized_image_may_be_resized']}
    else:raise ValueError('Original adapter unsupported')
    if not 1<=count<=MAX_UNITS:raise ValueError('Inventory unit limit')
    return {'adapter_revision':REVISION,'source_sha256':hashlib.sha256(data).hexdigest(),
        'unit_count':count,'units':[{'unit':i,'kind':kind} for i,kind in enumerate(kinds,1)],**fidelity}


def read_structured(data,mime,unit):
    if mime!=DOCX:raise ValueError('Structured adapter unsupported')
    blocks,fidelity=docx_body(data)
    if type(unit) is not int or not 1<=unit<=len(blocks):raise ValueError('Unit unavailable')
    block=blocks[unit-1]
    if block.tag not in {W+'p',W+'tbl',W+'sectPr'}:raise ValueError('Unsupported OOXML body block')
    # Drawing/math/merged-cell XML is preserved, not flattened into a guessed reading order.
    xml=ET.tostring(block,encoding='unicode')
    if len(xml)>60000:raise ValueError('Original unit too large; cannot silently truncate')
    visual=any(n.tag.rsplit('}',1)[-1] in {'drawing','pict','object','txbxContent'} for n in block.iter())
    return {'unit':unit,'kind':block.tag.rsplit('}',1)[-1],'original_xml':xml,
        'text':'\n'.join(n.text or '' for n in block.iter(W+'t')),
        'needs_visual':visual,**fidelity}



def index_navigation(data,mime,units):
    """No OCR or provider calls. Errors are per-unit; raw original stays available."""
    from app.core.config import get_settings
    from app.services.document_pipeline import run_pipeline
    result={}
    pdf=None
    if mime=='application/pdf':
        from pypdf import PdfReader
        pdf=PdfReader(io.BytesIO(data),strict=True)
    for unit in units:
        stage='NATIVE_EXTRACTION'
        try:
            if pdf is not None:
                page=pdf.pages[unit-1]
                stream=page.get_contents()
                if stream is not None and len(stream.get_data())>8*1024*1024:raise ValueError('Stream limit')
                text=page.extract_text() or ''
                import re
                painted=bool(stream and re.search(rb'(?:^|\s)(?:Do|S|s|f|f\*|B|B\*)(?:\s|$)',stream.get_data()))
                resources=page.get('/Resources',{})
                resources=resources.get_object() if hasattr(resources,'get_object') else resources
                if resources.get('/XObject') or painted:
                    result[str(unit)]={'status':'NEEDS_VISUAL','reason':'Page contains visual content requiring full extraction.'}
                    continue
                location={'page':unit,'extraction_method':'native_navigation'}
            elif mime==DOCX:
                block=read_structured(data,mime,unit)
                if block['needs_visual']:
                    result[str(unit)]={'status':'NEEDS_VISUAL','reason':'Embedded image requires full extraction.'}
                    continue
                if block['kind']=='sectPr' or not block['text'].strip():
                    result[str(unit)]={'status':'INDEXED','chunks':[],'quality':'empty_structure'}
                    continue
                text=block['text'];location={'body_block':unit,'extraction_method':'native_navigation'}
            else:
                text='';location={'image_id':'original'}
            if not text.strip():
                result[str(unit)]={'status':'NEEDS_VISUAL','reason':'No native navigation text; original read remains on demand.'}
                continue
            if len(text)>60000:raise ValueError('Navigation unit limit')
            stage='NAVIGATION_EMBEDDING'
            chunks=run_pipeline(data,'',get_settings().rag_model_dir,hashlib.sha256(data).hexdigest(),
                units=[(text,{**location,'original_unit':unit,'authority':'derived_navigation'})])
            if len(chunks)>=1000:raise ValueError('Unit chunk limit')
            result[str(unit)]={'status':'INDEXED','chunks':chunks,'quality':'native_unverified_navigation',
                'original_read_by_model':False}
        except Exception:
            result[str(unit)]={'status':'ERROR','error_code':stage+'_FAILED',
                'reason':'Bounded native indexing failed; original preserved.'}
    return {'units':result}

if __name__=='__main__':
    import sys
    from pathlib import Path
    source,output,mime,action,unit=sys.argv[1:]
    data=Path(source).read_bytes()
    if action=='index':
        value=index_navigation(data,mime,json.loads(unit))
    else:
        value=inventory(data,mime) if action=='inventory' else read_structured(data,mime,int(unit))
    Path(output).write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')
