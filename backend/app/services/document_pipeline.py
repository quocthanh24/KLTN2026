"""Bounded PDF/DOCX extraction, optional page Vision and real E5 inference."""
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile
from app.core.document_errors import PipelineFailure
from app.services.document_content import PageContent

MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_FILE = "onnx/model_qint8_avx512_vnni.onnx"
MAX_TEXT = 2_000_000
MAX_CHUNKS = 2000
ARTIFACT_HASHES = {
    MODEL_FILE: "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88",
    "tokenizer.json": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
}


def e5_vector(session, encoded):
    """Shared masked mean pooling and L2 normalization for passages/queries."""
    import numpy as np
    feed={name:np.array([values],dtype=np.int64) for name,values in (
        ('input_ids',encoded.ids),('attention_mask',encoded.attention_mask),('token_type_ids',encoded.type_ids))}
    output=session.run(None,{item.name:feed[item.name] for item in session.get_inputs()})[0]
    mask=feed['attention_mask'][...,None]
    vector=(output*mask).sum(axis=1)[0]/mask.sum()
    norm=np.linalg.norm(vector)
    if vector.shape!=(384,) or not np.isfinite(vector).all() or norm<=0:
        raise ValueError('Invalid E5 embedding')
    return (vector/norm).tolist()


def extract(data: bytes, extension: str):
    units = []
    if extension in {'.jpg', '.jpeg', '.png'}:
        from app.services.document_vision import extract_page, normalize_image
        value = extract_page(normalize_image(data))
        text = value['text']
        if value['visual_description'].strip():
            text += '\n[Visual description]\n' + value['visual_description']
        units.append(PageContent(text, {'image_id': 'original', 'visual': True},
            extraction_method='vision', visual_regions=[{'id': 'original', 'kind': 'image'}]).unit())
    elif extension == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise PipelineFailure('DOCUMENT_PASSWORD_PROTECTED')
        if len(reader.pages)>300:
            raise PipelineFailure('DOCUMENT_PAGE_LIMIT')
        from app.core.config import get_settings
        from app.services.document_vision import extract_page, render_page
        settings = get_settings()
        calls = 0
        for page_no, page in enumerate(reader.pages, 1):
            stream = page.get_contents()
            if stream and len(stream.get_data()) > 8 * 1024 * 1024:
                raise ValueError("PDF content limit")
            text = page.extract_text() or ""
            resources = page.get('/Resources', {})
            resources = resources.get_object() if hasattr(resources, 'get_object') else resources
            objects = resources.get('/XObject', {})
            objects = objects.get_object() if hasattr(objects, 'get_object') else objects
            # Images/forms can contain scans or meaningful graphics. Vector path
            # painting also needs visual extraction even on a text-layer page.
            import re
            painted = bool(stream and re.search(rb'(?:^|\s)(?:Do|S|s|f|f\*|B|B\*)(?:\s|$)', stream.get_data()))
            visual = bool(objects) or painted
            location = {'page': page_no, 'extraction_method': 'native'}
            if visual or not text.strip():
                # Truly empty pages need no network call.
                if visual or text.strip() or (stream and stream.get_data().strip()):
                    if len(reader.pages) > settings.document_vision_max_pages:
                        raise PipelineFailure('DOCUMENT_PAGE_LIMIT')
                    calls += 1
                    if calls > settings.document_vision_max_calls:
                        raise PipelineFailure('DOCUMENT_VISION_CALL_CAP')
                    try:
                        value = extract_page(render_page(data, page_no))
                    except PipelineFailure as failure:
                        failure.page_number = page_no
                        raise
                    # A vision transcript replaces OCR-like duplicate native text.
                    text = value['text']
                    if value['visual_description'].strip():
                        text += '\n[Visual description]\n' + value['visual_description']
                    location.update(extraction_method='vision', visual=True, region_id=f'page-{page_no}')
            units.append(PageContent(text,location,page_number=page_no,
                extraction_method=location['extraction_method'],
                visual_regions=[{'id':f'page-{page_no}','kind':'page'}] if location.get('visual') else []).unit())
            if sum(len(value) for value, _ in units) > MAX_TEXT:
                raise ValueError("PDF text limit")
    elif extension == ".docx":
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        # Recheck ZIP/XML limits before invoking python-docx on a stored object.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(i.file_size for i in entries) > 64 * 1024 * 1024:
                raise ValueError("DOCX limits")
            for entry in entries:
                if entry.flag_bits & 1:
                    raise ValueError("Encrypted ZIP")
                if entry.filename.endswith(".xml"):
                    if entry.file_size > 8 * 1024 * 1024:
                        raise ValueError("XML limit")
                    xml = archive.read(entry)
                    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                        raise ValueError("Unsafe XML")
        document = Document(io.BytesIO(data))
        if document.element.xpath('.//w:tbl//w:tbl'):
            raise PipelineFailure('DOCX_LAYOUT_UNSUPPORTED')
        if document.element.xpath('.//*[local-name()="pict" or local-name()="object" or local-name()="txbxContent"]'):
            raise PipelineFailure('DOCX_IMAGES_UNSUPPORTED')
        if any(any(token in rel.reltype for token in ('/chart','/diagram','/oleObject','/package')) for rel in document.part.rels.values()):
            raise PipelineFailure('DOCX_IMAGES_UNSUPPORTED')
        if any(not drawing.xpath('.//a:blip') for drawing in document.element.xpath('.//w:drawing')):
            raise PipelineFailure('DOCX_IMAGES_UNSUPPORTED')
        if any('/header' in r.reltype or '/footer' in r.reltype for r in document.part.rels.values()
               if not r.is_external and any('/image' in v.reltype for v in r.target_part.rels.values())):
            raise PipelineFailure('DOCX_IMAGES_UNSUPPORTED')
        from app.core.config import get_settings
        from app.services.document_vision import extract_page,normalize_image
        settings=get_settings();processed_images=set();calls=0
        def images(element,locator):
            nonlocal calls
            result=[]
            for rel_id in element.xpath('.//a:blip/@r:embed'):
                if rel_id in processed_images:
                    continue
                rel=document.part.rels.get(rel_id)
                if rel is None or rel.is_external or '/image' not in rel.reltype:
                    raise PipelineFailure('DOCX_IMAGES_UNSUPPORTED')
                calls+=1
                if calls>min(settings.document_vision_max_pages,settings.document_vision_max_calls):
                    raise PipelineFailure('DOCUMENT_VISION_CALL_CAP')
                value=extract_page(normalize_image(rel.target_part.blob))
                text=value['text']
                if value['visual_description'].strip():
                    text+='\n[Visual description]\n'+value['visual_description']
                processed_images.add(rel_id)
                if text.strip():
                    result.append(PageContent(text,{**locator,'region_id':rel_id,'embedded_image_id':rel_id},
                        extraction_method='vision',visual_regions=[{'id':rel_id,'kind':'embedded_image'}]).unit())
            if element.xpath('.//a:blip/@r:link'):
                raise PipelineFailure('DOCX_IMAGES_UNSUPPORTED')
            return result
        for block_no, block in enumerate(document.iter_inner_content(), 1):
            if isinstance(block, Paragraph):
                units.append(PageContent(block.text,{"paragraph":block_no}).unit())
                units.extend(images(block._p,{'paragraph':block_no}))
            elif isinstance(block, Table):
                for row_no, row in enumerate(block.rows, 1):
                    for cell_no, cell in enumerate(row.cells, 1):
                        units.append(PageContent(cell.text,{"table_block":block_no,"row":row_no,"cell":cell_no}).unit())
                        units.extend(images(cell._tc,{'table_block':block_no,'row':row_no,'cell':cell_no}))
        all_images={key for key,rel in document.part.rels.items() if '/image' in rel.reltype}
        # Unsupported locations (e.g. background artwork) must not silently pass.
        if all_images-processed_images:
            raise PipelineFailure('DOCX_IMAGES_UNSUPPORTED')
        seen_parts=set()
        for section_no,section in enumerate(document.sections,1):
            for kind,container in (('header',section.header),('footer',section.footer)):
                key=str(container.part.partname)
                if key in seen_parts:continue
                seen_parts.add(key)
                if container._element.xpath('.//w:tbl//w:tbl'):
                    raise PipelineFailure('DOCX_LAYOUT_UNSUPPORTED')
                for block_no,block in enumerate(container.iter_inner_content(),1):
                    locator={'part':kind,'section':section_no}
                    if isinstance(block,Paragraph):
                        units.append(PageContent(block.text,{**locator,'paragraph':block_no}).unit())
                    elif isinstance(block,Table):
                        for row_no,row in enumerate(block.rows,1):
                            for cell_no,cell in enumerate(row.cells,1):
                                units.append(PageContent(cell.text,{**locator,'table_block':block_no,'row':row_no,'cell':cell_no}).unit())
    else:
        raise ValueError("Unsupported format")
    if not any(text.strip() for text, _ in units):
        raise PipelineFailure('PDF_NO_TEXT' if extension == '.pdf' else 'DOCUMENT_NO_TEXT')
    if sum(len(text) for text, _ in units) > MAX_TEXT:
        raise ValueError('Text limit')
    return units


def run_pipeline(data, extension, model_dir, checksum, *, units=None):
    if hashlib.sha256(data).hexdigest() != checksum:
        raise PipelineFailure("EXTRACTION_FAILED")
    try:
        units = extract(data, extension) if units is None else units
    except PipelineFailure:
        raise
    except Exception:
        raise PipelineFailure("EXTRACTION_FAILED") from None
    try:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        root = Path(model_dir)
        for filename, expected in ARTIFACT_HASHES.items():
            digest = hashlib.sha256()
            with (root / filename).open("rb") as artifact:
                while part := artifact.read(1024 * 1024):
                    digest.update(part)
            if digest.hexdigest() != expected:
                raise ValueError("Pinned model artifact mismatch")
        tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(root / MODEL_FILE), sess_options=options, providers=["CPUExecutionProvider"])
        chunks = []
        # Preserve offsets in extracted page/paragraph text; never invent DOCX pages.
        for text, location in units:
            offsets = tokenizer.encode(text, add_special_tokens=False).offsets
            for token_start in range(0, len(offsets), 272):
                window = offsets[token_start:token_start + 320]
                start, end = window[0][0], window[-1][1]
                content = text[start:end]
                if not content.strip():
                    continue
                encoded = tokenizer.encode("passage: " + content)
                if len(encoded.ids) > 512:
                    raise ValueError("Token budget exceeded")
                vector=e5_vector(session,encoded)
                chunks.append({"content": content, "source_location": {**location, "char_start": start, "char_end": end},
                    "embedding": vector, "model_revision": MODEL_REVISION})
                if len(chunks) > MAX_CHUNKS:
                    raise ValueError("Chunk limit exceeded")
                if token_start + 320 >= len(offsets):
                    break
        if not chunks:
            raise ValueError("No chunks")
        return chunks
    except Exception:
        raise PipelineFailure("VECTOR_PROCESSING_FAILED") from None


if __name__ == "__main__":
    source, output, extension, model_dir, checksum = sys.argv[1:]
    try:
        result = {"chunks": run_pipeline(Path(source).read_bytes(), extension, model_dir, checksum)}
    except PipelineFailure as failure:
        result = {"error": str(failure), "page_number": getattr(failure, 'page_number', None), "exception_type": type(failure.__context__).__name__ if failure.__context__ else type(failure).__name__}
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
