"""UC29 knowledge eligibility primitive, not an AI Q&A endpoint."""
from sqlalchemy import select
from app.models.learning_document import DocumentChunk, LearningDocument, DocumentStatus
from app.services.channel_service import ChannelService


class DocumentRetrieval(ChannelService):
    async def search(self, principal, channel_id, embedding, limit=5):
        async with self.transaction(principal):
            await self.channel_access(channel_id, principal.user.id)
            return list(await self.db.scalars(select(DocumentChunk).join(LearningDocument).where(
                LearningDocument.channel_id == channel_id, LearningDocument.status == DocumentStatus.READY,
                LearningDocument.deleted_at.is_(None))
                .order_by(DocumentChunk.embedding.cosine_distance(embedding)).limit(min(max(limit, 1), 20))))
