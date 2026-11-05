"use client";
import Link from "next/link";
import { useEffect,useState } from "react";
import { createApiClient } from "@/lib/api";
import { chatAuth,clearAuth } from "@/features/auth/store";
import { useAuth } from "@/features/auth/provider";
export const adminApi=createApiClient({...chatAuth,clear:clearAuth});
type MediaOperation={id:string;state:'PENDING'|'COMPLETE'|'ERROR';error_code?:string|null};
type RecordItem={id:string;full_name?:string;email?:string;name?:string;status?:string;system_role?:string;admin_version:number;disabled_at?:string|null;owner?:{full_name:string;email:string}|null;deleted_at?:string|null;media_revocation?:MediaOperation};
type Page<T>={items:T[];next_offset:number|null};
export function AdminNav(){return <nav><Link href="/app">Trang chính</Link><Link href="/admin">Tổng quan quản trị</Link><Link href="/admin/users">Người dùng</Link><Link href="/admin/workspaces">Workspace toàn hệ thống</Link><Link href="/admin/reports">Báo cáo vi phạm</Link></nav>;}
export function ManagementView({kind}:{kind:'users'|'workspaces'}) {
  const {user}=useAuth();const [search,setSearch]=useState(''),[filter,setFilter]=useState(''),[offset,setOffset]=useState(0),[attempt,setAttempt]=useState(0);
  const [state,setState]=useState<{key:string;data?:Page<RecordItem>;error?:string}|null>(null),[detail,setDetail]=useState<RecordItem|null>(null),[pending,setPending]=useState(false),[notice,setNotice]=useState('');
  const key=`${kind}/${search}/${filter}/${offset}/${attempt}`;
  const [operation,setOperation]=useState<MediaOperation|null>(null);
  useEffect(()=>{if(!operation||operation.state!=='PENDING')return;let active=true;const timer=setTimeout(()=>{
    adminApi<MediaOperation>(`/admin/media-revocations/${operation.id}`,{protected:true}).then(next=>{if(active){setOperation(next);setNotice(next.state==='COMPLETE'?'Đã hoàn tất thu hồi media.':next.error_code?'Chưa xác nhận ngắt media; hệ thống đang thử lại.':'Đang thu hồi media…');}})
      .catch(()=>{if(active){setNotice('Chưa lấy được xác nhận thu hồi media.');setOperation({...operation});}});
  },1000);return()=>{active=false;clearTimeout(timer);};},[operation]);
  useEffect(()=>{let active=true;adminApi<Page<RecordItem>>(`/admin/${kind}?search=${encodeURIComponent(search)}&offset=${offset}${filter?`&status=${filter}`:''}`,{protected:true}).then(data=>{if(active)setState({key,data});}).catch(e=>{if(active)setState({key,error:e.message});});return()=>{active=false;};},[key,kind,search,filter,offset]);
  const current=state?.key===key?state:null;
  if(user?.system_role!=='ADMIN')return <p role="alert">Bạn không có quyền quản trị hệ thống.</p>;
  async function select(item:RecordItem){try{setNotice('');const selected=await adminApi<RecordItem>(`/admin/${kind}/${item.id}`,{protected:true});setDetail(selected);setOperation(selected.media_revocation||null);}catch(e){setNotice(e instanceof Error?e.message:'Không thể tải chi tiết');}}
  async function change(){if(!detail||pending||operation?.state==='PENDING')return;const enabled=kind==='users'?detail.status!=='ACTIVE':!!detail.disabled_at;
    const reason=window.prompt('Lý do thay đổi trạng thái (ít nhất 3 ký tự)');if(!reason||reason.trim().length<3)return;
    if(!window.confirm(`${enabled?'Kích hoạt / khôi phục':'Khóa / vô hiệu hóa'} “${detail.full_name||detail.name}”?`))return;
    setPending(true);try{const updated=await adminApi<RecordItem>(`/admin/${kind}/${detail.id}/state`,{method:'PATCH',protected:true,body:{version:detail.admin_version,enabled,reason}});setDetail(updated);setOperation(updated.media_revocation||null);setAttempt(n=>n+1);setNotice(updated.media_revocation?'Trạng thái đã lưu. Đang chờ xác nhận ngắt media…':'Đã cập nhật trạng thái.');}
    catch(e){const message=e instanceof Error?e.message:'Không thể cập nhật';await select(detail);setNotice(message);}finally{setPending(false);}}
  return <><AdminNav/><h1>{kind==='users'?'Quản lý người dùng':'Quản lý Workspace'}</h1><label>Tìm kiếm<input value={search} maxLength={120} onChange={e=>{setSearch(e.target.value);setOffset(0);setDetail(null);}}/></label>
    <label>Trạng thái<select value={filter} onChange={e=>{setFilter(e.target.value);setOffset(0);}}><option value="">Tất cả</option><option value="ACTIVE">Hoạt động</option><option value={kind==='users'?'LOCKED':'DISABLED'}>{kind==='users'?'Bị khóa':'Vô hiệu hóa'}</option></select></label>
    {!current&&<p role="status">Đang tải…</p>}{current?.error&&<p role="alert">{current.error}<button onClick={()=>setAttempt(n=>n+1)}>Thử lại</button></p>}
    {current?.data&&<><ul>{current.data.items.map(item=><li key={item.id}><button onClick={()=>void select(item)}>{item.full_name||item.name}</button><p>{item.email} · {item.status||(item.disabled_at?'DISABLED':'ACTIVE')}</p></li>)}</ul>{!current.data.items.length&&<p>Không có kết quả.</p>}<div className="actions"><button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-50))}>Trang trước</button><button disabled={current.data.next_offset===null} onClick={()=>setOffset(current.data!.next_offset!)}>Trang sau</button></div></>}
    {notice&&<p role="status">{notice}</p>}{detail&&<section aria-label="Chi tiết quản trị"><h2>{detail.full_name||detail.name}</h2><p>{detail.email} · {detail.status||(detail.disabled_at?'DISABLED':'ACTIVE')}</p>{detail.owner&&<p>Chủ sở hữu: {detail.owner.full_name} ({detail.owner.email})</p>}<p>Phiên bản trạng thái: {detail.admin_version}</p>
      {operation&&<p role="status">Thu hồi media: {operation.state==='PENDING'?'Đang chờ xác nhận':operation.state==='COMPLETE'?'Đã xác nhận hoàn tất':'Chưa hoàn tất'}</p>}
      {!(kind==='users'&&detail.system_role==='ADMIN')&&!detail.deleted_at&&<button disabled={pending||operation?.state==='PENDING'} onClick={()=>void change()}>Thay đổi trạng thái</button>}<button onClick={()=>setDetail(null)}>Đóng chi tiết</button></section>}</>;
}
