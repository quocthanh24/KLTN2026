from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, File, Form, UploadFile, Response, Depends, Query
from urllib.parse import quote
from app.api.dependencies import CurrentPrincipal, DbSession
from app.schemas.common import DataResponse
from app.schemas.learning_document import DocumentResponse, DocumentUpdate
from app.services.learning_document_service import LearningDocumentService

def private_headers(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["learning-documents"], dependencies=[Depends(private_headers)])


@router.get("/channels/{channel_id}/learning-documents", response_model=DataResponse[list[DocumentResponse]])
async def list_documents(channel_id: UUID, principal: CurrentPrincipal, db: DbSession):
    return {"data": await LearningDocumentService(db).list_documents(principal, channel_id)}


@router.post("/channels/{channel_id}/learning-documents", status_code=201, response_model=DataResponse[DocumentResponse])
async def upload_document(channel_id: UUID, principal: CurrentPrincipal, db: DbSession,
    file: Annotated[UploadFile, File()], name: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None, upload_id: Annotated[UUID | None, Form()] = None):
    return {"data": await LearningDocumentService(db).upload(principal, channel_id, file, name, description, upload_id)}


@router.get("/learning-documents/{document_id}/original")
async def original(document_id: UUID, principal: CurrentPrincipal, db: DbSession):
    return {"data": {"url": await LearningDocumentService(db).original_url(principal, document_id)}}


@router.get('/learning-documents/{document_id}/inventory')
async def inventory(document_id: UUID, principal: CurrentPrincipal, db: DbSession, channel_id: UUID | None = None, revision: str | None = Query(None,pattern='^[0-9a-f]{64}$')):
    return {'data': await LearningDocumentService(db).original_inventory(principal,document_id,channel_id,revision)}


@router.get('/learning-documents/{document_id}/blocks/{unit}')
async def original_block(document_id: UUID, unit: int, principal: CurrentPrincipal, db: DbSession, channel_id: UUID | None = None, revision: str | None = Query(None,pattern='^[0-9a-f]{64}$')):
    return {'data': await LearningDocumentService(db).original_block(principal,document_id,unit,channel_id,revision)}


@router.post('/learning-documents/{document_id}/retry', response_model=DataResponse[DocumentResponse])
async def retry(document_id: UUID, principal: CurrentPrincipal, db: DbSession, channel_id: UUID | None = None, units: Annotated[list[int] | None, Query()] = None):
    return {'data': await LearningDocumentService(db).retry_processing(principal, document_id, channel_id, units)}


@router.get("/learning-documents/{document_id}", response_model=DataResponse[DocumentResponse])
async def detail(document_id: UUID, principal: CurrentPrincipal, db: DbSession, channel_id: UUID | None = None):
    return {"data": await LearningDocumentService(db).detail(principal, document_id, channel_id)}


@router.patch("/learning-documents/{document_id}", response_model=DataResponse[DocumentResponse])
async def update(document_id: UUID, request: DocumentUpdate, principal: CurrentPrincipal, db: DbSession, channel_id: UUID | None = None):
    return {"data": await LearningDocumentService(db).update_document(principal, document_id, request, channel_id)}


@router.delete("/learning-documents/{document_id}", status_code=204)
async def delete_document(document_id: UUID, principal: CurrentPrincipal, db: DbSession, channel_id: UUID | None = None):
    await LearningDocumentService(db).delete_document(principal, document_id, channel_id)
    return Response(status_code=204)


def file_response(content, inline=False):
    data, mime, filename = content
    fallback = ''.join(c for c in filename if 32 <= ord(c) < 127 and c not in '"\\') or "download"
    disposition = f'{"inline" if inline else "attachment"}; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename, safe="")}'
    return Response(data, media_type=mime, headers={"Content-Disposition": disposition,
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.get("/learning-documents/{document_id}/download")
async def download(document_id: UUID, principal: CurrentPrincipal, db: DbSession, channel_id: UUID | None = None):
    return file_response(await LearningDocumentService(db).content(principal, document_id, channel_id))


@router.get("/learning-documents/{document_id}/preview")
async def preview(document_id: UUID, principal: CurrentPrincipal, db: DbSession, channel_id: UUID | None = None):
    return file_response(await LearningDocumentService(db).content(principal, document_id, channel_id, preview=True), inline=True)


@router.get('/learning-documents/{document_id}/pages/{page_number}/image')
async def page_image(document_id: UUID, page_number: int, principal: CurrentPrincipal, db: DbSession, channel_id: UUID | None = None, revision: str | None = Query(None,pattern='^[0-9a-f]{64}$')):
    data = await LearningDocumentService(db).page_image(principal, document_id, page_number, channel_id,revision)
    return Response(data, media_type='image/jpeg', headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/learning-documents/{document_id}/regions/{region_id}/image')
async def region_image(document_id:UUID,region_id:str,principal:CurrentPrincipal,db:DbSession,channel_id:UUID|None=None):
    data=await LearningDocumentService(db).region_image(principal,document_id,region_id,channel_id)
    return Response(data,media_type='image/jpeg',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.get('/learning-documents/{document_id}/image')
async def original_image(document_id:UUID,principal:CurrentPrincipal,db:DbSession,channel_id:UUID|None=None):
    data=await LearningDocumentService(db).original_image(principal,document_id,channel_id)
    return Response(data,media_type='image/jpeg',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
