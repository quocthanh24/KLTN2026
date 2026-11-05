"""Private originals are cleaned only after durable DB revocation."""
from datetime import UTC, datetime
from sqlalchemy import select
from app.models.learning_document import LearningDocument
from app.services.storage_service import get_storage_service


async def cleanup_deleted(db, storage=None, document_id=None):
    async with db.begin():
        query = select(LearningDocument).where(LearningDocument.deleted_at.is_not(None), LearningDocument.object_deleted_at.is_(None))
        if document_id is not None:
            query = query.where(LearningDocument.id == document_id)
        document = await db.scalar(query.order_by(LearningDocument.updated_at, LearningDocument.id).with_for_update(skip_locked=True).limit(1))
        if not document:
            return False
        try:
            await (storage or get_storage_service()).delete(document.object_key)
        except Exception:
            document.updated_at = datetime.now(UTC)  # Rotate failures so one bad key cannot starve other cleanup jobs.
            return False  # Tombstone is durable; next worker pass retries idempotently.
        document.object_deleted_at = datetime.now(UTC)
    return True
