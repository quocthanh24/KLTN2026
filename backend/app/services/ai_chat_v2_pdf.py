"""Bounded private PDF physical inventory, isolated from the web process.

No OCR/model/network call, no source text output, no document mutation.
"""
import io
import json
import sys
from pathlib import Path
from app.core.learning_documents import MAX_DOCUMENT_BYTES


def physical_inventory(data):
    from pypdf import PdfReader
    if len(data)>MAX_DOCUMENT_BYTES:raise ValueError('PDF byte limit')
    reader=PdfReader(io.BytesIO(data),strict=True)
    if reader.is_encrypted or not 1<=len(reader.pages)<=300:raise ValueError('PDF page/encryption limit')
    blank=[]
    for number,page in enumerate(reader.pages,1):
        stream=page.get_contents()
        # pypdf content streams can be empty dictionaries WITH non-empty bytes.
        # Truthiness would falsely classify every such page as blank.
        raw=stream.get_data() if stream is not None else b''
        if len(raw)>8*1024*1024:raise ValueError('PDF stream limit')
        if not raw.strip():blank.append(number)
    return {'physical_pages':list(range(1,len(reader.pages)+1)),'proven_blank_pages':blank,
        'interpretation':'physical inventory only; not OCR/semantic understanding'}


if __name__=='__main__':
    source,output=sys.argv[1:]
    Path(output).write_text(json.dumps(physical_inventory(Path(source).read_bytes())),encoding='utf8')
