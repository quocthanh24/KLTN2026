"""Temporary owned conversation/session only; never modifies original chat."""
import asyncio,json,secrets,sys
from datetime import UTC,datetime,timedelta
from pathlib import Path
from uuid import UUID,uuid4
from sqlalchemy import delete
from app.db.session import session_factory
from app.models import AuthSession,Channel
from app.models.ai_conversation import AIConversation
from app.core.config import get_settings
from app.core.security import hash_refresh_token

STATE=Path('D:/Temp/kltn-inventory-browser.json')
async def main():
    async with session_factory() as db,db.begin():
        if '--cleanup' in sys.argv:
            data=json.loads(STATE.read_text())
            await db.execute(delete(AIConversation).where(AIConversation.id==UUID(data['conversation'])))
            await db.execute(delete(AuthSession).where(AuthSession.id==UUID(data['session'])))
            STATE.unlink()
            print('Temporary conversation/session removed')
            return
        original=await db.get(AIConversation,UUID('cda1f170-494b-4fc5-b389-d44621d49182'))
        channel=await db.get(Channel,original.channel_id)
        sid,cid=uuid4(),uuid4();raw=secrets.token_urlsafe(48)
        db.add(AuthSession(id=sid,user_id=original.user_id,refresh_token_hash=hash_refresh_token(raw),expires_at=datetime.now(UTC)+timedelta(minutes=30)))
        db.add(AIConversation(id=cid,user_id=original.user_id,channel_id=channel.id,title='Inventory browser verification'))
        STATE.write_text(json.dumps({'session':str(sid),'conversation':str(cid),'workspace':str(channel.workspace_id),
                         'cookie_name':get_settings().refresh_cookie_name,'cookie':raw}),encoding='utf-8')
        print('Temporary browser fixture created')
if __name__=='__main__':
    asyncio.run(main())
