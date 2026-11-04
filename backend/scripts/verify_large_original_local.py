"""Read-only original/citation audit on local development. No model dispatch.

Reuses the existing uploader's active session and normal service authorization.
Requires the owner's authorized document ID; does not create sessions or permissions.
An owned audit conversation may be created solely to exercise DocumentTools.
"""
import argparse
import asyncio
import base64
import copy
import hashlib
import json
import sys
from datetime import UTC,datetime
from pathlib import Path
from uuid import UUID
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


async def run(args):
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from app.core.config import get_settings
    from app.db.session import session_factory,engine
    from app.models.learning_document import LearningDocument
    from app.models.auth_session import AuthSession
    from app.models.ai_conversation import AIConversation
    from app.services.auth_service import AuthService
    from app.services.ai_conversation_service import AIConversationService
    from app.services.ai_chat_v2_tools import DocumentTools
    from app.services.ai_chat_v2_original import validate_original_reference
    from app.core.exceptions import AppError
    settings=get_settings();url=make_url(str(settings.database_url))
    if settings.app_env!='development' or url.host not in {'localhost','127.0.0.1'}:
        raise RuntimeError('Authorized local development only')
    import openai
    def forbidden(*a,**k):raise AssertionError('PAID API FORBIDDEN')
    openai.OpenAI=openai.AsyncOpenAI=forbidden
    try:
        async with session_factory() as db:
            async with db.begin():
                doc=await db.get(LearningDocument,args.document)
                if doc is None or doc.deleted_at is not None:raise RuntimeError('Document unavailable')
                session=await db.scalar(select(AuthSession).where(AuthSession.user_id==doc.uploader_id,
                    AuthSession.revoked_at.is_(None),AuthSession.expires_at>datetime.now(UTC)).order_by(AuthSession.created_at.desc()))
                if session is None:raise RuntimeError('Existing authorized session required')
                principal=await AuthService(db).load_principal(doc.uploader_id,session.id)
                identity,channel,revision,status=doc.id,doc.channel_id,doc.checksum,doc.status.value
                cid=await db.scalar(select(AIConversation.id).where(AIConversation.channel_id==channel,
                    AIConversation.user_id==principal.user.id,AIConversation.title=='Original-reader audit (no model calls)'))
            service=AIConversationService(db)
            if cid is None:
                conv=await service.create(principal,channel);cid=conv['id']
                await service.rename(principal,channel,cid,'Original-reader audit (no model calls)')
            tools=DocumentTools(service,principal,channel,cid,settings=settings)
            inv=await tools.call('source_inventory',{'document_id':str(identity),'revision':revision})
            try:await tools.call('read_original_units',{'document_id':str(identity),'revision':revision,'units':[args.page]})
            except AppError as error:capability=error.code
            else:raise RuntimeError('This audit expects actual Vision to remain OFF')
            dry=DocumentTools(service,principal,channel,cid,settings=settings.model_copy(update={'ai_chat_v2_vision_ready':True}))
            packet=await dry.call('read_original_units',{'document_id':str(identity),'revision':revision,'units':[args.page]})
            reference=packet['passages'][0]
            image=next(p for p in packet['_image_parts'] if p.get('type')=='input_image')
            data=base64.b64decode(image['image_url'].split(',',1)[1])
            assert hashlib.sha256(data).hexdigest()==reference['representation_sha256']
            async with service.transaction(principal):await validate_original_reference(service,principal,channel,reference)
            wrong=copy.deepcopy(reference);wrong['location']={'page':args.page+1,'physical_page':args.page+1}
            try:
                async with service.transaction(principal):await validate_original_reference(service,principal,channel,wrong)
            except AppError as error:wrong_code=error.code
            else:raise AssertionError('Forged locator accepted')
            proof={'actual_document_id':str(identity),'original_sha256':revision,'physical_units':inv['unit_count'],
                'actual_status':status,'actual_vision_off_error':capability,'render_only_dry_run':True,
                'global_vision_flag_unchanged':not get_settings().ai_chat_v2_vision_ready,'model_calls':0,
                'original_tool_unit':args.page,'representation_sha256':reference['representation_sha256'],
                'image_part_prepared':True,'provider_received_real_request':False,
                'citation_original_validation':'PASS_PROVENANCE_ONLY','forged_locator_error':wrong_code,
                'audit_conversation_id':str(cid),'new_session_or_permission':False,'semantic_coverage':'NOT_ASSESSED'}
            target=Path(args.evidence);target.mkdir(parents=True,exist_ok=True)
            (target/'actual-v2-original-tool-proof.json').write_text(json.dumps(proof,indent=2),encoding='utf8')
            print(json.dumps(proof,indent=2))
    finally:await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document',type=UUID,required=True)
    parser.add_argument('--page',type=int,default=6)
    parser.add_argument('--evidence',required=True)
    asyncio.run(run(parser.parse_args()))
