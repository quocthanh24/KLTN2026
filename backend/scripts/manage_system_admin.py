"""Trusted DB-operator CLI, existing account UUID only. No password or public endpoint."""
import argparse
import asyncio
from uuid import UUID
from sqlalchemy import func,select,text
from app.db.session import session_factory,engine
from app.models import User
from app.models.enums import AccountStatus,SystemRole
from app.models.moderation import AdministrationAudit


async def change(identity,grant,reason):
    async with session_factory() as db,db.begin():
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('system-administration',0))"))
        user=await db.get(User,identity,with_for_update=True,populate_existing=True)
        if user is None or user.status!=AccountStatus.ACTIVE:raise ValueError('Requires an existing ACTIVE account UUID')
        desired=SystemRole.ADMIN if grant else SystemRole.USER
        if user.system_role==desired:return
        if not grant:
            active=await db.scalar(select(func.count()).select_from(User).where(User.system_role==SystemRole.ADMIN,User.status==AccountStatus.ACTIVE))
            if active<=1:raise ValueError('Cannot remove the last active system administrator')
        before=str(user.system_role);user.system_role=desired;user.admin_version+=1
        db.add(AdministrationAudit(actor_id=None,target_type='USER',target_id=identity,action='CLI_SYSTEM_ROLE',
            before={'system_role':before},after={'system_role':str(desired),'source':'trusted_database_operator_cli'},reason=reason))
    await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('user_id',type=UUID)
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--grant',action='store_true');mode.add_argument('--revoke',action='store_true')
    parser.add_argument('--reason',required=True)
    args=parser.parse_args()
    if not 3<=len(args.reason.strip())<=1000:parser.error('Reason needs 3-1000 characters')
    asyncio.run(change(args.user_id,args.grant,args.reason));print('System role updated; audited; no credentials changed')
