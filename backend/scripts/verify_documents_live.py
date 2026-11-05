"""Scoped PostgreSQL/MinIO evidence and cleanup for unique UC29 browser fixtures."""
import asyncio
import base64
import hashlib
import io
import json
import re
import sys
import zipfile
from urllib.request import urlopen
from urllib.error import HTTPError
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import get_settings
from app.models import User, Workspace, Channel, AuthSession, WorkspaceMember, WorkspaceInvitation
from app.models.learning_document import LearningDocument, DocumentChunk, DocumentStatus
from app.services.storage_service import get_storage_service


async def main(data):
    marker = data["marker"]
    assert re.fullmatch(r"phase5a-[0-9a-f-]{36}", marker)
    if data["action"] == "word_fixture":
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Real UC29 learning document for asynchronous upload.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
        print(base64.b64encode(body.getvalue()).decode())
        return
    if data["action"] == "blank_pdf":
        from pypdf import PdfWriter
        body = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(400, 400)
        writer.write(body)
        print(base64.b64encode(body.getvalue()).decode())
        return
    engine = create_async_engine(str(get_settings().database_url))
    storage = get_storage_service()
    try:
        async with async_sessionmaker(engine)() as db:
            workspace = await db.scalar(select(Workspace).where(Workspace.name == marker))
            users = list(await db.scalars(select(User).where(User.email.in_([f"{marker}-{r}@example.com" for r in ("owner", "member", "outsider")]))))
            channels = list(await db.scalars(select(Channel).where(Channel.workspace_id == workspace.id))) if workspace else []
            documents = list(await db.scalars(select(LearningDocument).where(LearningDocument.channel_id.in_([c.id for c in channels]))))
            if data["action"] == "cleanup":
                for document in documents:
                    await storage.delete(document.object_key)
                if workspace:
                    await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == workspace.id))
                    await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id))
                    await db.execute(delete(Workspace).where(Workspace.id == workspace.id, Workspace.name == marker))
                if users:
                    await db.execute(delete(AuthSession).where(AuthSession.user_id.in_([u.id for u in users])))
                    await db.execute(delete(User).where(User.id.in_([u.id for u in users])))
                await db.commit()
                print(f"Scoped UC29 cleanup PASS: {len(documents)} originals removed")
                return
            assert workspace and len(documents) == len(data["documents"])
            for expected in data["documents"]:
                document = next(d for d in documents if str(d.id) == expected["id"])
                assert str(document.channel_id) == data["channel_id"]
                assert str(document.uploader_id) == data["uploader_id"]
                assert document.name == expected["name"] and document.description == expected["description"]
                expected_status = expected.get("status", "READY")
                assert document.status.value == expected_status
                assert document.processing_error == expected.get("processing_error")
                chunks = list(await db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id)))
                if expected_status == "READY":
                    assert chunks and all(len(chunk.embedding) == 384 for chunk in chunks)
                    assert all(chunk.content.strip() and chunk.source_location for chunk in chunks)
                else:
                    assert not chunks
                head = storage.client.head_object(Bucket=storage.bucket, Key=document.object_key)
                assert head["ContentLength"] == document.size_bytes and head["ContentType"] == document.content_type
                response = storage.client.get_object(Bucket=storage.bucket, Key=document.object_key)
                try:
                    assert hashlib.sha256(response["Body"].read()).hexdigest() == document.checksum == expected["checksum"]
                finally:
                    response["Body"].close()
                # Anonymous object access must fail; do not print endpoint/object keys.
                url = get_settings().object_storage_endpoint.rstrip("/") + "/" + storage.bucket + "/" + document.object_key
                try:
                    with urlopen(url, timeout=5):
                        raise AssertionError("Object is public")
                except HTTPError as exc:
                    assert exc.code == 403
            print(f"PostgreSQL status/chunks/vector(384)/source/channel/uploader + MinIO original bytes/private access PASS ({len(documents)} documents)")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main(json.load(sys.stdin)))
    except Exception as exc:
        print(type(exc).__name__)
        sys.exit(1)
