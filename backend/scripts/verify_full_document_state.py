"""Read only the reported document and turn; save hashes/counts, no source text."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
from uuid import UUID
import httpx
from pypdf import PdfReader
from sqlalchemy import select
from app.core.config import get_settings
from app.db.session import session_factory
from app.models.learning_document import LearningDocument, DocumentChunk
from app.models.ai_conversation import AITurn

ROOT=Path(__file__).resolve().parents[2]
async def main():
    auth=json.loads(Path('D:/Temp/kltn-master-ai/state.json').read_text())
    identity=UUID('dc77b96b-f794-4569-8da3-f476b191aac0')
    async with session_factory() as db:
        doc=await db.get(LearningDocument,identity)
        assert str(doc.uploader_id)==auth['owner']
        chunks=list(await db.scalars(select(DocumentChunk).where(DocumentChunk.document_id==identity).order_by(DocumentChunk.ordinal)))
        original=await db.get(AITurn,UUID('7d969802-e0da-4cbc-8b04-c726d923e75e'))
        result={'document_id':str(identity),'channel_id':str(doc.channel_id),'status':doc.status.value,
            'chunks':len(chunks),'characters':sum(len(c.content) for c in chunks),
            'pages':sorted({c.source_location['page'] for c in chunks}),
            'original_reported_turn_status':original.status,
            'chunk_snapshot':[{'id':str(c.id),'ordinal':c.ordinal,'revision':c.model_revision,
                'location':c.source_location,'sha256':hashlib.sha256(c.content.encode()).hexdigest()} for c in chunks]}
    async with httpx.AsyncClient(timeout=60) as client:
        response=await client.get(f'http://localhost:8027/api/v1/learning-documents/{identity}/download',
            params={'channel_id':result['channel_id']},headers={'Authorization':'Bearer '+auth['token']})
        response.raise_for_status()
        result['physical_pdf_pages']=len(PdfReader(io.BytesIO(response.content)).pages)
        result['pdf_sha256']=hashlib.sha256(response.content).hexdigest()
    settings=get_settings()
    result['runtime']={'provider':settings.ai_provider,'model':settings.openai_model,
        'turn_calls':settings.ai_turn_max_calls,'turn_timeout':settings.ai_turn_timeout_seconds,
        'vision_enabled':settings.document_vision_enabled}
    (ROOT/'evidence/full-document-coverage/document-state.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'chunk_snapshot','pdf_sha256'}}))
if __name__=='__main__':asyncio.run(main())
