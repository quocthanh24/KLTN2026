"""Upload container validation, not text extraction or RAG processing."""
import hashlib
from pathlib import PurePath
from tempfile import SpooledTemporaryFile
import unicodedata
import zipfile
from xml.etree import ElementTree
from app.core.exceptions import AppError

MAX_DOCUMENT_BYTES = 200 * 1024 * 1024
DOCUMENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


async def validate_document(file):
    filename = (file.filename or "").strip()
    if not filename or len(filename) > 255 or any(c in filename for c in "/\\:") or any(unicodedata.category(c).startswith("C") for c in filename):
        raise AppError(422, "DOCUMENT_FILENAME_INVALID", "Tên tệp không hợp lệ")
    extension = PurePath(filename).suffix.lower()
    mime = (file.content_type or "").split(";", 1)[0].strip().lower()
    if extension not in DOCUMENT_TYPES or mime != DOCUMENT_TYPES[extension]:
        raise AppError(422, "DOCUMENT_FORMAT_UNSUPPORTED", "Chỉ hỗ trợ PDF, DOCX, JPG và PNG")
    body = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    size = 0
    digest = hashlib.sha256()
    try:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_DOCUMENT_BYTES:
                raise AppError(413, "DOCUMENT_TOO_LARGE", "Tệp vượt quá 200 MiB")
            digest.update(chunk)
            body.write(chunk)
        if not size:
            raise ValueError("empty")
        body.seek(0)
        if extension == ".pdf":
            if not body.read(8).startswith(b"%PDF-"):
                raise ValueError("signature")
            body.seek(max(0, size - 1024))
            if b"%%EOF" not in body.read():
                raise ValueError("truncated")
        elif extension in {'.jpg', '.jpeg', '.png'}:
            signature = body.read(8)
            if not (signature.startswith(b'\xff\xd8\xff') if extension != '.png' else signature == b'\x89PNG\r\n\x1a\n'):
                raise ValueError('signature')
        else:
            with zipfile.ZipFile(body) as archive:
                entries = archive.infolist()
                if len(entries) > 1000 or sum(i.file_size for i in entries) > 64 * 1024 * 1024:
                    raise ValueError("archive limits")
                for name in ("[Content_Types].xml", "word/document.xml"):
                    info = archive.getinfo(name)
                    if info.file_size > 8 * 1024 * 1024 or info.flag_bits & 1:
                        raise ValueError("XML limits")
                    xml = archive.read(name)
                    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                        raise ValueError("DTD")
                    root = ElementTree.fromstring(xml)
                    if name == "word/document.xml" and root.tag != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document":
                        raise ValueError("document root")
        body.seek(0)
        return filename, extension, mime, body, size, digest.hexdigest()
    except AppError:
        body.close()
        raise
    except (ValueError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError, ElementTree.ParseError):
        body.close()
        raise AppError(422, "DOCUMENT_INVALID", "Tệp rỗng, hỏng hoặc không phải tài liệu hợp lệ") from None
    except BaseException:
        body.close()
        raise
