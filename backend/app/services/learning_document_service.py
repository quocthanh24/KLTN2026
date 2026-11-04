import logging
import hashlib
from datetime import UTC, datetime
from uuid import uuid4
from sqlalchemy import select, delete
from app.core.exceptions import AppError
from app.core.learning_documents import MAX_DOCUMENT_BYTES, validate_document
from app.models.learning_document import LearningDocument, DocumentChunk, DocumentStatus
from app.models.user import User
from app.schemas.learning_document import DocumentResponse
from app.services.channel_service import ChannelService
from app.services.storage_service import get_storage_service
from app.services.document_progressive import metadata as derived_metadata

logger = logging.getLogger(__name__)


class LearningDocumentService(ChannelService):
    async def original_image(self, principal, identity, channel_id=None):
        from app.services.document_processing import render_private_page
        async with self.transaction(principal):
            document = await self.access_document(principal, identity, channel_id=channel_id)
            if document.content_type not in {'image/jpeg', 'image/png'}:
                raise AppError(422, 'DOCUMENT_IMAGE_INVALID', 'Tài liệu không phải ảnh JPG/PNG.')
            data = await self.original_bytes(document)
        try:
            image = await render_private_page(data, 'original')
        except Exception:
            raise AppError(503, 'DOCUMENT_RENDER_FAILED', 'Không thể đọc ảnh tài liệu.') from None
        async with self.transaction(principal):
            await self.access_document(principal, identity, channel_id=channel_id)
        return image

    async def region_image(self,principal,identity,region_id,channel_id=None):
        from app.services.document_processing import render_private_page
        async with self.transaction(principal):
            document=await self.access_document(principal,identity,channel_id=channel_id)
            if document.status!=DocumentStatus.READY or document.content_type!='application/vnd.openxmlformats-officedocument.wordprocessingml.document':
                raise AppError(422,'DOCUMENT_REGION_INVALID','Vùng ảnh không khả dụng.')
            exists=await self.db.scalar(select(DocumentChunk.id).where(DocumentChunk.document_id==identity,
                DocumentChunk.source_location['embedded_image_id'].as_string()==region_id).limit(1))
            if not exists:
                raise AppError(404,'DOCUMENT_REGION_INVALID','Không tìm thấy vùng ảnh trong chỉ mục.')
            data=await self.original_bytes(document)
        try:
            image=await render_private_page(data,region_id)
        except Exception:
            raise AppError(503,'DOCUMENT_RENDER_FAILED','Không thể đọc ảnh nhúng.') from None
        async with self.transaction(principal):
            await self.access_document(principal,identity,channel_id=channel_id)
        return image

    async def page_image(self, principal, identity, page_number, channel_id=None, revision=None):
        from app.services.document_processing import render_private_page
        async with self.transaction(principal):
            document = await self.access_document(principal, identity, channel_id=channel_id)
            if document.content_type != 'application/pdf' or not 1 <= page_number <= 10000:
                raise AppError(422, 'DOCUMENT_PAGE_INVALID', 'Trang PDF không hợp lệ.')
            if revision is not None and document.checksum!=revision:
                raise AppError(409,'DOCUMENT_SOURCE_CHANGED','Revision của tham chiếu không còn khả dụng.')
            revision=document.checksum
            data = await self.original_bytes(document)
        try:
            result = await render_private_page(data, page_number)
        except Exception:
            raise AppError(503, 'DOCUMENT_RENDER_FAILED', 'Không thể đọc ảnh trang tài liệu.') from None
        # Permissions and tombstone are checked again after rendering.
        async with self.transaction(principal):
            current=await self.access_document(principal, identity, channel_id=channel_id)
            if current.checksum!=revision:
                raise AppError(409,'DOCUMENT_SOURCE_CHANGED','Revision đã thay đổi khi mở trang gốc.')
        return result

    async def retry_processing(self, principal, identity, channel_id=None, units=None):
        async with self.transaction(principal):
            document = await self.access_document(principal, identity, self.MANAGERS, channel_id)
            if document.status == DocumentStatus.PROCESSING:
                return DocumentResponse.model_validate(document).model_copy(update={'can_manage': True, 'derived_state':await derived_metadata(self.db,document)})
            from app.core.config import get_settings
            if get_settings().document_progressive_enabled:
                from app.models.document_derivation import DocumentDerivation
                from app.services.document_progressive import retry_units
                saved=await self.db.get(DocumentDerivation,identity)
                if saved is not None and saved.revision == document.checksum:
                    if units is not None:
                        await retry_units(self.db,document,units)
                    else:
                        from app.services.document_progressive import full_pipeline_blocker
                        blocker=full_pipeline_blocker(document,saved.state)
                        if blocker:
                            raise AppError(409,blocker,'Không thể xử lý lại: '+blocker+'. Cần quản trị viên kiểm tra cấu hình OCR/Vision hoặc giới hạn xử lý.')
                        import copy
                        state=copy.deepcopy(saved.state)
                        state['full_pipeline_requested']=True
                        saved.state=state
                        document.status=DocumentStatus.PROCESSING
                else:
                    if units is not None:raise AppError(409,'DOCUMENT_PROGRESS_UNAVAILABLE','Chưa có inventory để chọn phần.')
                    document.status=DocumentStatus.PROCESSING
                document.processed_at=None
                document.processing_token=document.processing_lease_until=None
                document.processing_error=None
                await self.db.flush()
                await self.db.refresh(document)
                return DocumentResponse.model_validate(document).model_copy(update={'can_manage':True,'derived_state':await derived_metadata(self.db,document)})
            if units is not None:raise AppError(422,'DOCUMENT_PROGRESS_DISABLED','Retry theo phần chưa được bật.')
            if document.status != DocumentStatus.FAILED:
                raise AppError(409, 'DOCUMENT_RETRY_NOT_FAILED', 'Chỉ xử lý lại tài liệu thất bại.')
            document.status = DocumentStatus.PROCESSING
            document.processing_error = None
            document.processed_at = None
            document.processing_token = document.processing_lease_until = None
            await self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == identity))
            await self.db.flush()
            await self.db.refresh(document)
            return DocumentResponse.model_validate(document).model_copy(update={'can_manage': True})

    def __init__(self, db, storage=None):
        super().__init__(db)
        self.storage = storage

    async def list_documents(self, principal, channel_id):
        async with self.transaction(principal):
            channel = await self.channel_access(channel_id, principal.user.id)
            membership = await self.members.get_membership(channel.workspace_id, principal.user.id)
            rows = await self.db.execute(select(LearningDocument, User.full_name).join(User, User.id == LearningDocument.uploader_id).where(
                LearningDocument.channel_id == channel_id, LearningDocument.deleted_at.is_(None)).order_by(LearningDocument.created_at.desc(), LearningDocument.id))
            return [DocumentResponse.model_validate(d).model_copy(update={"derived_state":await derived_metadata(self.db,d), "uploader_name": full_name, "can_manage": membership.role in self.MANAGERS}) for d, full_name in rows]

    async def upload(self, principal, channel_id, file, name=None, description=None, upload_id=None):
        body = None
        stored = False
        key = None
        storage = None
        try:
            async with self.transaction(principal):
                await self.channel_access(channel_id, principal.user.id, self.MANAGERS)
                filename, extension, mime, body, size, checksum = await validate_document(file)
                name = (name or "").strip() or filename
                description = (description or "").strip() or None
                if len(name) > 255 or description and len(description) > 2000:
                    raise AppError(422, "DOCUMENT_METADATA_INVALID", "Tên tối đa 255 ký tự, mô tả tối đa 2000 ký tự")
                identity = upload_id or uuid4()
                existing = await self.db.get(LearningDocument, identity)
                if existing:
                    if existing.deleted_at is not None:
                        raise AppError(404, "DOCUMENT_NOT_FOUND", "Tài liệu đã bị xóa")
                    if (existing.channel_id, existing.uploader_id, existing.checksum, existing.name, existing.description, existing.original_filename) != (channel_id, principal.user.id, checksum, name, description, filename):
                        raise AppError(409, "DOCUMENT_UPLOAD_CONFLICT", "Mã tải lên đã được sử dụng; hãy chọn lại tệp")
                    return DocumentResponse.model_validate(existing).model_copy(update={"can_manage": True})
                key = f"learning-documents/{channel_id}/{identity.hex}{extension}"
                try:
                    storage = self.storage or get_storage_service()
                    await storage.upload(key, body, mime)
                    stored = True
                except Exception:
                    # Multipart failures can leave an object even if the client saw an error.
                    if storage:
                        await self.compensate(storage, key)
                    raise AppError(503, "DOCUMENT_STORAGE_FAILED", "Không thể lưu tài liệu. Vui lòng thử lại") from None
                document = LearningDocument(id=identity, channel_id=channel_id, uploader_id=principal.user.id,
                    original_filename=filename, name=name, description=description, content_type=mime,
                    size_bytes=size, object_key=key, checksum=checksum)
                self.db.add(document)
                await self.db.flush()
                result = DocumentResponse.model_validate(document).model_copy(update={"can_manage": True,
                    "uploader_name": (await self.db.get(User, principal.user.id)).full_name})
            return result
        except BaseException:
            if stored:
                await self.compensate(storage, key)
            raise
        finally:
            if body is not None:
                body.close()
            await file.close()

    @staticmethod
    async def compensate(storage, key):
        try:
            await storage.delete(key)
        except Exception:
            # Object UUID is safe to log; credentials and signed URLs are never logged.
            logger.error("Document orphan cleanup required for object %s", key)

    async def original_url(self, principal, document_id):
        async with self.transaction(principal):
            document = await self.access_document(principal, document_id)
            await self.original_bytes(document)
            return f"/api/v1/learning-documents/{document_id}/download"

    async def access_document(self, principal, identity, allowed=None, channel_id=None, include_deleted=False):
        candidate = await self.db.get(LearningDocument, identity)
        if candidate is None or channel_id is not None and candidate.channel_id != channel_id:
            raise AppError(404, "DOCUMENT_NOT_FOUND", "Không tìm thấy tài liệu")
        await self.channel_access(candidate.channel_id, principal.user.id, allowed)
        document = await self.db.scalar(select(LearningDocument).where(LearningDocument.id == identity).with_for_update().execution_options(populate_existing=True))
        if document is None or document.deleted_at is not None and not include_deleted:
            raise AppError(404, "DOCUMENT_NOT_FOUND", "Tài liệu đã bị xóa hoặc không còn khả dụng")
        return document

    async def detail(self, principal, identity, channel_id=None):
        async with self.transaction(principal):
            document = await self.access_document(principal, identity, channel_id=channel_id)
            from app.models.channel import Channel
            channel = await self.db.get(Channel, document.channel_id)
            membership = await self.members.get_membership(channel.workspace_id, principal.user.id)
            uploader = await self.db.get(User, document.uploader_id)
            return DocumentResponse.model_validate(document).model_copy(update={"derived_state":await derived_metadata(self.db,document), "uploader_name": uploader.full_name, "can_manage": membership.role in self.MANAGERS})

    async def original_bytes(self, document):
        from botocore.exceptions import ClientError
        try:
            data=await (self.storage or get_storage_service()).read_document(document.object_key, MAX_DOCUMENT_BYTES)
            if len(data)!=document.size_bytes or hashlib.sha256(data).hexdigest()!=document.checksum:
                raise AppError(409,'DOCUMENT_SOURCE_CHANGED','Bản gốc không khớp kích thước/checksum đã lưu.')
            return data
        except AppError:
            raise
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "NotFound", "404"}:
                raise AppError(404, "DOCUMENT_FILE_UNAVAILABLE", "Tệp tài liệu không còn khả dụng") from None
            raise AppError(503, "DOCUMENT_STORAGE_FAILED", "Không thể lấy nội dung tài liệu. Vui lòng thử lại") from None
        except Exception:
            raise AppError(503, "DOCUMENT_STORAGE_FAILED", "Không thể lấy nội dung tài liệu. Vui lòng thử lại") from None

    async def content(self, principal, identity, channel_id=None, preview=False):
        async with self.transaction(principal):
            document = await self.access_document(principal, identity, channel_id=channel_id)
            if preview and document.content_type != "application/pdf":
                raise AppError(415, "DOCUMENT_PREVIEW_UNSUPPORTED", "Tài liệu này chưa hỗ trợ xem trực tiếp. Hãy tải xuống để xem")
            return await self.original_bytes(document), document.content_type, document.original_filename

    async def original_inventory(self, principal, identity, channel_id=None, revision=None):
        from app.services.ai_chat_v2_original import private_adapter
        async with self.transaction(principal):
            document=await self.access_document(principal,identity,channel_id=channel_id)
            if revision is not None and document.checksum!=revision:
                raise AppError(409,'DOCUMENT_SOURCE_CHANGED','Revision của tham chiếu không còn khả dụng.')
            raw=await self.original_bytes(document)
            revision,mime=document.checksum,document.content_type
        result=await private_adapter(raw,mime)
        async with self.transaction(principal):
            document=await self.access_document(principal,identity,channel_id=channel_id)
            if document.checksum!=revision:raise AppError(409,'DOCUMENT_SOURCE_CHANGED','Revision đã thay đổi.')
        return {k:v for k,v in result.items() if k!='units'} | {'revision':revision,'original_read_by_model':False}

    async def original_block(self, principal, identity, unit, channel_id=None, revision=None):
        from app.services.ai_chat_v2_original import private_adapter
        from app.services.original_document_adapter import DOCX
        async with self.transaction(principal):
            document=await self.access_document(principal,identity,channel_id=channel_id)
            if document.content_type!=DOCX or not 1<=unit<=10000:
                raise AppError(422,'DOCUMENT_BLOCK_INVALID','Khối DOCX không hợp lệ.')
            if revision is not None and document.checksum!=revision:
                raise AppError(409,'DOCUMENT_SOURCE_CHANGED','Revision của tham chiếu không còn khả dụng.')
            revision=document.checksum
            raw=await self.original_bytes(document)
        result=await private_adapter(raw,DOCX,'read',unit)
        async with self.transaction(principal):
            current=await self.access_document(principal,identity,channel_id=channel_id)
            if current.checksum!=revision:
                raise AppError(409,'DOCUMENT_SOURCE_CHANGED','Revision đã thay đổi khi mở khối gốc.')
        return {**result,'revision':revision,'authority':'original','original_read_by_model':False}

    async def update_document(self, principal, identity, request, channel_id=None):
        async with self.transaction(principal):
            document = await self.access_document(principal, identity, self.MANAGERS, channel_id)
            document.name, document.description = request.name, request.description
            await self.db.flush()
            await self.db.refresh(document)
            result = DocumentResponse.model_validate(document).model_copy(update={"derived_state":await derived_metadata(self.db,document), "can_manage": True, "uploader_name": (await self.db.get(User, document.uploader_id)).full_name})
        return result

    async def delete_document(self, principal, identity, channel_id=None):
        async with self.transaction(principal):
            document = await self.access_document(principal, identity, self.MANAGERS, channel_id, include_deleted=True)
            if document.deleted_at is None:
                document.deleted_at = datetime.now(UTC)
                document.processing_token = document.processing_lease_until = None
                await self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == identity))
        from app.services.document_cleanup import cleanup_deleted
        # DB revocation has committed before any attempt to delete the original.
        await cleanup_deleted(self.db, self.storage, identity)
