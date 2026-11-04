"""Read-only real DB/MinIO verification; no document text or credentials in output."""
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID
from scripts import run_multimodal_verification_server  # dedicated test DB guard
from sqlalchemy import select
from app.db.session import session_factory
from app.models import Channel
from app.models.learning_document import LearningDocument, DocumentChunk
from app.services.storage_service import get_storage_service

OUT=Path(__file__).resolve().parents[2]/'docs/progress/evidence/multimodal-document-rag'


async def main():
    report=json.loads((OUT/'browser.json').read_text(encoding='utf-8'))
    results=[]
    async with session_factory() as db:
        documents=list(await db.scalars(select(LearningDocument).join(Channel).where(
            Channel.workspace_id==UUID(report['workspace']),LearningDocument.deleted_at.is_(None))))
        for document in documents:
            chunks=list(await db.scalars(select(DocumentChunk).where(DocumentChunk.document_id==document.id)))
            original=await get_storage_service().read_document(document.object_key,25*1024*1024)
            results.append({'id':str(document.id),'name':document.name,'status':document.status.value,
                'original_checksum_valid':hashlib.sha256(original).hexdigest()==document.checksum,
                'original_bytes':len(original),'chunks':len(chunks),
                'unique_ordinals':len({c.ordinal for c in chunks})==len(chunks),
                'pages':sorted({c.source_location['page'] for c in chunks if 'page' in c.source_location}),
                'methods':sorted({c.source_location.get('extraction_method','native') for c in chunks}),
                'valid_vectors':all(len(c.embedding)==384 and abs(sum(float(x)**2 for x in c.embedding)-1)<.0001 for c in chunks)})
    (OUT/'db-minio.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))


if __name__=='__main__':
    asyncio.run(main())
