"use client";
import {useEffect,useState} from 'react';
import {useAuth} from '@/features/auth/provider';
import {adminApi,AdminNav} from './views';
import {PdfViewer} from '@/features/learning-documents/pdf-viewer';
type Report={id:string;target_type:string;reason:string;description:string|null;status:string;version:number;created_at:string;reporter_id:string|null;outcome:string|null;action:string|null;resolution:string|null;resolved_at:string|null;target_available?:boolean;target?:{content?:string;name?:string;content_type?:string}|null};
export function ReportsView(){
  const {user}=useAuth();const [status,setStatus]=useState('PENDING'),[offset,setOffset]=useState(0),[attempt,setAttempt]=useState(0);
  const [state,setState]=useState<{key:string;items?:Report[];next?:number|null;error?:string}|null>(null),[detail,setDetail]=useState<Report|null>(null),[notice,setNotice]=useState(''),[pending,setPending]=useState(false),[preview,setPreview]=useState<File|null>(null);
  const key=`${status}/${offset}/${attempt}`;
  useEffect(()=>{let active=true;adminApi<{items:Report[];next_offset:number|null}>(`/admin/reports?status=${status}&offset=${offset}`,{protected:true}).then(data=>{if(active)setState({key,items:data.items,next:data.next_offset});}).catch(e=>{if(active)setState({key,error:e.message});});return()=>{active=false;};},[key,status,offset]);
  if(user?.system_role!=='ADMIN')return <p role="alert">Bạn không có quyền quản trị hệ thống.</p>;
  const current=state?.key===key?state:null;
  async function open(id:string){try{setPreview(null);setNotice('');setDetail(await adminApi<Report>(`/admin/reports/${id}`,{protected:true}));}catch(e){setNotice(e instanceof Error?e.message:'Không thể tải báo cáo');}}
  async function decide(outcome:'NO_VIOLATION'|'VIOLATION'){
    if(!detail||pending)return;const reason=window.prompt('Kết luận và lý do xử lý (ít nhất 3 ký tự)');if(!reason||reason.trim().length<3)return;
    const action=outcome==='NO_VIOLATION'?'NONE':detail.target_available?'HIDE':'ALREADY_UNAVAILABLE';
    if(!window.confirm(`Xác nhận ${outcome==='NO_VIOLATION'?'không vi phạm':action==='HIDE'?'có vi phạm và ẩn nội dung':'có vi phạm, đối tượng đã không còn khả dụng'}?`))return;
    setPending(true);try{await adminApi(`/admin/reports/${detail.id}/resolve`,{method:'POST',protected:true,body:{version:detail.version,outcome,action,reason}});await open(detail.id);setAttempt(n=>n+1);setNotice('Đã xử lý báo cáo.');}
    catch(e){const message=e instanceof Error?e.message:'Không thể xử lý';await open(detail.id);setNotice(message);}finally{setPending(false);}
  }
  return <><AdminNav/><h1>Báo cáo vi phạm</h1><label>Lọc báo cáo<select value={status} onChange={e=>{setStatus(e.target.value);setOffset(0);setDetail(null);}}><option value="PENDING">Chờ xử lý</option><option value="RESOLVED">Đã xử lý</option></select></label>
    {!current&&<p role="status">Đang tải…</p>}{current?.error&&<p role="alert">{current.error}<button onClick={()=>setAttempt(n=>n+1)}>Thử lại</button></p>}
    {current?.items&&<><ul>{current.items.map(r=><li key={r.id} data-report-id={r.id}><button onClick={()=>void open(r.id)}>{r.target_type} · {r.reason}</button><p>{new Date(r.created_at).toLocaleString('vi-VN')} · {r.status}</p></li>)}</ul>{!current.items.length&&<p>Không có báo cáo trong nhóm này.</p>}<button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-50))}>Trang trước</button><button disabled={current.next==null} onClick={()=>setOffset(current.next!)}>Trang sau</button></>}
    {notice&&<p role="status">{notice}</p>}{detail&&<section aria-label="Chi tiết báo cáo"><h2>{detail.target_type} · {detail.reason}</h2><p>Người gửi: {detail.reporter_id||'Tài khoản không còn tồn tại'}</p><p>{detail.description||'Không có mô tả bổ sung.'}</p><p>{detail.status} · {detail.outcome} · {detail.resolution}</p>
      {detail.resolved_at&&<p>Đã xử lý: {new Date(detail.resolved_at).toLocaleString('vi-VN')}</p>}
      {!detail.target_available&&<p>Nội dung đã xóa hoặc không còn hiển thị.</p>}{detail.target?.content&&<blockquote>{detail.target.content}</blockquote>}{detail.target?.name&&<p>{detail.target.name}</p>}
      {detail.target?.name&&<button disabled={pending} onClick={async()=>{setPending(true);try{const file=await adminApi<File>(`/admin/reports/${detail.id}/evidence`,{protected:true,download:true});setPreview(file);}catch(e){setNotice(e instanceof Error?e.message:'Không thể mở tài liệu');}finally{setPending(false);}}}>Mở tài liệu báo cáo</button>}
      {preview?.type==='application/pdf'&&<PdfViewer file={preview}/>}
      {preview&&<button onClick={()=>{const url=URL.createObjectURL(preview),link=document.createElement('a');link.href=url;link.download=preview.name;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}}>Tải bản gốc để kiểm tra</button>}
      {detail.status==='PENDING'&&<div className="actions"><button disabled={pending} onClick={()=>void decide('NO_VIOLATION')}>Không vi phạm</button><button disabled={pending} onClick={()=>void decide('VIOLATION')}>Có vi phạm</button></div>}
      <button onClick={()=>{setDetail(null);setPreview(null);}}>Để lại / đóng báo cáo</button></section>}</>;
}
