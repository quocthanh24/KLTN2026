"""Read-only UC29 diagnosis: no source text, credentials or object URLs emitted."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
from sqlalchemy import select, func
from pypdf import PdfReader
from app.core.config import get_settings
from app.db.session import session_factory
from app.models.learning_document import LearningDocument, DocumentChunk, DocumentStatus
from app.models.document_derivation import DocumentDerivation
from app.services.storage_service import get_storage_service
from app.services.document_progressive import metadata


async def main():
    settings=get_settings()
    report={'runtime':{'progressive_enabled':settings.document_progressive_enabled,
        'vision_enabled':settings.document_vision_enabled,
        'vision_key_present':bool(settings.openai_api_key.get_secret_value().strip()),
        'model':settings.openai_model,'vision_page_limit':settings.document_vision_max_pages,
        'vision_call_limit':settings.document_vision_max_calls},'documents':[]}
    async with session_factory() as db:
        report['pending_documents']=await db.scalar(select(func.count()).select_from(LearningDocument).where(
            LearningDocument.deleted_at.is_(None),LearningDocument.status==DocumentStatus.PROCESSING))
        rows=(await db.execute(select(LearningDocument,DocumentDerivation).join(DocumentDerivation,
            LearningDocument.id==DocumentDerivation.document_id).where(LearningDocument.deleted_at.is_(None)))).all()
        for doc,derived in rows:
            if len(derived.state.get('units',{}))!=81 or doc.content_type!='application/pdf':continue
            raw=await get_storage_service().read_document(doc.object_key,200*1024*1024)
            pdf=PdfReader(io.BytesIO(raw))
            report['documents'].append({'id':str(doc.id),'status':doc.status.value,
                'processing_error':doc.processing_error,'processing_lease_until':str(doc.processing_lease_until),
                'physical_pages':len(pdf.pages),'native_text_lengths':[len((p.extract_text() or '').strip()) for p in pdf.pages],
                'checksum_matches':hashlib.sha256(raw).hexdigest()==doc.checksum,
                'chunk_count':await db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id==doc.id)),
                'derived_state':await metadata(db,doc)})
    output=Path(__file__).resolve().parents[2]/'evidence/uc29-processing-20261103/diagnosis.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':asyncio.run(main())
