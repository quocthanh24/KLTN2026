"""Operator-only scoped retry; real pipeline, no original/chunk deletion upfront."""
import argparse
import asyncio
import json
from uuid import UUID
from sqlalchemy import select,func
from app.db.session import session_factory,engine
from app.models.learning_document import LearningDocument,DocumentChunk
from app.services.document_processing import process_pending

async def main(identity):
    async with session_factory() as db:
        changed=await process_pending(db,document_id=identity,retry_failed=True)
        async with db.begin():
            doc=await db.get(LearningDocument,identity,populate_existing=True)
            if doc is None:raise ValueError('Document not found')
            count=await db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id==identity))
            print(json.dumps({'id':str(identity),'claimed':bool(changed),'status':doc.status.value,'error':doc.processing_error,'chunks':count}))
    await engine.dispose()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('document_id',type=UUID)
    asyncio.run(main(parser.parse_args().document_id))
