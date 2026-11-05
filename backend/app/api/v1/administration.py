from fastapi import APIRouter, Response, Query
from uuid import UUID
from typing import Literal
from datetime import date
from app.services.administration_analytics import AdministrationAnalytics
from app.api.dependencies import CurrentPrincipal,DbSession
from app.schemas.administration import ReportCreate,StateChange,ReportDecision
from app.services.administration_service import AdministrationService
from app.services.report_service import ReportService
router=APIRouter(tags=['administration'])


@router.post('/violation-reports',status_code=201)
async def create_report(request:ReportCreate,principal:CurrentPrincipal,db:DbSession,response:Response):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await ReportService(db).create(principal,request)}


@router.get('/admin/users')
async def users(principal:CurrentPrincipal,db:DbSession,response:Response,search:str=Query('',max_length=120),
                status:Literal['ACTIVE','LOCKED']|None=None,offset:int=Query(0,ge=0,le=100000)):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await AdministrationService(db).users_list(principal,search,status,offset)}


@router.get('/admin/users/{identity}')
async def user_detail(identity:UUID,principal:CurrentPrincipal,db:DbSession,response:Response):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await AdministrationService(db).user_detail(principal,identity)}


@router.patch('/admin/users/{identity}/state')
async def user_state(identity:UUID,request:StateChange,principal:CurrentPrincipal,db:DbSession,response:Response):
    response.headers['Cache-Control']='private, no-store'
    result=await AdministrationService(db).user_state(principal,identity,request)
    if result.get('media_revocation'):response.status_code=202
    return {'data':result}


@router.get('/admin/workspaces')
async def workspaces(principal:CurrentPrincipal,db:DbSession,response:Response,search:str=Query('',max_length=120),
                     status:Literal['ACTIVE','DISABLED']|None=None,offset:int=Query(0,ge=0,le=100000)):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await AdministrationService(db).workspaces_list(principal,search,status,offset)}


@router.get('/admin/workspaces/{identity}')
async def workspace_detail(identity:UUID,principal:CurrentPrincipal,db:DbSession,response:Response):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await AdministrationService(db).workspace_detail(principal,identity)}


@router.patch('/admin/workspaces/{identity}/state')
async def workspace_state(identity:UUID,request:StateChange,principal:CurrentPrincipal,db:DbSession,response:Response):
    response.headers['Cache-Control']='private, no-store'
    result=await AdministrationService(db).workspace_state(principal,identity,request)
    if result.get('media_revocation'):response.status_code=202
    return {'data':result}


@router.get('/admin/reports')
async def reports(principal:CurrentPrincipal,db:DbSession,response:Response,status:Literal['PENDING','RESOLVED']|None='PENDING',offset:int=Query(0,ge=0,le=100000)):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await AdministrationService(db).reports_list(principal,status,offset)}


@router.get('/admin/reports/{identity}')
async def report_detail(identity:UUID,principal:CurrentPrincipal,db:DbSession,response:Response):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await AdministrationService(db).report_detail(principal,identity)}


@router.post('/admin/reports/{identity}/resolve')
async def resolve(identity:UUID,request:ReportDecision,principal:CurrentPrincipal,db:DbSession,response:Response):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await AdministrationService(db).resolve(principal,identity,request)}


@router.get('/admin/reports/{identity}/evidence')
async def evidence(identity:UUID,principal:CurrentPrincipal,db:DbSession):
    from app.api.v1.learning_documents import file_response
    return file_response(await AdministrationService(db).evidence(principal,identity),inline=True)


@router.get('/admin/dashboard')
async def dashboard(principal:CurrentPrincipal,db:DbSession,response:Response,start_date:date|None=None,end_date:date|None=None):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await AdministrationAnalytics(db).overview(principal,start_date,end_date)}


@router.get('/admin/dashboard/details')
async def dashboard_details(principal:CurrentPrincipal,db:DbSession,response:Response,
    group:Literal['users','workspaces','documents','study','reports'],start_date:date|None=None,end_date:date|None=None,offset:int=Query(0,ge=0,le=100000)):
    response.headers['Cache-Control']='private, no-store'
    return {'data':await AdministrationAnalytics(db).details(principal,group,start_date,end_date,offset)}
