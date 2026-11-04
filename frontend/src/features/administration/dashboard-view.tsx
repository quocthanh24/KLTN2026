"use client";
import {useEffect,useState} from 'react';
import {useAuth} from '@/features/auth/provider';
import {durationLabel,recentRange} from '@/features/learning-analytics/contracts';
import {AdminNav,adminApi} from './views';
type Dashboard={users:{total:number;active:number;locked:number;created_in_period:number};workspaces:{total:number;active:number;disabled:number;deleted:number;created_in_period:number};documents:{total:number;available:number;hidden:number;deleted:number;created_in_period:number};study:{duration_us:number;completed_sessions:number;excluded_sessions:number};reports:{pending:number;resolved:number;created_in_period:number;violation_in_period:number;no_violation_in_period:number};days:{day:string;duration_us:number}[]};
type Detail={id?:string;label?:string;status?:string;created_at?:string;day?:string;duration_us?:number};
export function DashboardView(){
  const {user}=useAuth();const [range,setRange]=useState(()=>recentRange(7)),[draft,setDraft]=useState(range),[attempt,setAttempt]=useState(0),[group,setGroup]=useState(''),[offset,setOffset]=useState(0);
  const [state,setState]=useState<{key:string;data?:Dashboard;error?:string}|null>(null),[details,setDetails]=useState<{key:string;items?:Detail[];next?:number|null;error?:string}|null>(null);
  const key=`${range.start}/${range.end}/${attempt}`,detailKey=`${key}/${group}/${offset}`;
  const query=`start_date=${range.start}&end_date=${range.end}`;
  useEffect(()=>{let active=true;adminApi<Dashboard>('/admin/dashboard?'+query,{protected:true}).then(data=>{if(active)setState({key,data});}).catch(e=>{if(active)setState({key,error:e.message});});return()=>{active=false;};},[key,query]);
  useEffect(()=>{let active=true;if(group)adminApi<{items:Detail[];next_offset:number|null}>(`/admin/dashboard/details?${query}&group=${group}&offset=${offset}`,{protected:true}).then(data=>{if(active)setDetails({key:detailKey,items:data.items,next:data.next_offset});}).catch(e=>{if(active)setDetails({key:detailKey,error:e.message});});return()=>{active=false;};},[detailKey,group,query,offset]);
  if(user?.system_role!=='ADMIN')return <p role="alert">Bạn không có quyền quản trị hệ thống.</p>;
  const current=state?.key===key?state:null,data=current?.data,drill=details?.key===detailKey?details:null;
  const labels:{[key:string]:string}={users:'Người dùng',workspaces:'Workspace',documents:'Tài liệu',study:'Hoạt động học tập',reports:'Báo cáo'};
  return <><AdminNav/><h1>Giám sát hệ thống</h1><p>Múi giờ Asia/Ho_Chi_Minh. Tổng hiện tại khác số tạo mới trong kỳ.</p>
    <div className="actions">{[7,30].map(n=><button key={n} onClick={()=>{const r=recentRange(n);setRange(r);setDraft(r);setOffset(0);}}>{n} ngày</button>)}</div>
    <form onSubmit={e=>{e.preventDefault();setRange(draft);setOffset(0);setAttempt(n=>n+1);}}><label>Từ ngày<input type="date" required max={draft.end} value={draft.start} onChange={e=>setDraft({...draft,start:e.target.value})}/></label><label>Đến ngày<input type="date" required min={draft.start} value={draft.end} onChange={e=>setDraft({...draft,end:e.target.value})}/></label><button>Áp dụng khoảng ngày</button></form>
    <button onClick={()=>setAttempt(n=>n+1)}>Cập nhật thống kê</button>{!current&&<p role="status">Đang tải thống kê…</p>}{current?.error&&<p role="alert">{current.error}<button onClick={()=>setAttempt(n=>n+1)}>Thử lại</button></p>}
    {data&&<>
      <section><h2>Người dùng</h2><p data-testid="admin-users-total">Hiện tại: {data.users.total}</p><p>Hoạt động: {data.users.active} · Bị khóa: {data.users.locked} · Tạo mới trong kỳ: {data.users.created_in_period}</p></section>
      <section><h2>Workspace</h2><p>Hiện tại: {data.workspaces.total} · Hoạt động: {data.workspaces.active} · Vô hiệu hóa: {data.workspaces.disabled} · Đã xóa: {data.workspaces.deleted}</p><p>Tạo trong kỳ: {data.workspaces.created_in_period}</p></section>
      <section><h2>Tài liệu</h2><p>Metadata: {data.documents.total} · Khả dụng: {data.documents.available} · Đã ẩn: {data.documents.hidden} · Đã xóa: {data.documents.deleted}</p><p>Tải lên trong kỳ: {data.documents.created_in_period}</p></section>
      <section><h2>Hoạt động học tập</h2><strong>{durationLabel(data.study.duration_us)}</strong><p>Thời gian tham gia đã chốt trong kỳ; loại trùng phòng trong từng người, cộng giữa những người khác nhau. Bao gồm lịch sử hợp lệ ở Workspace đã vô hiệu hóa/xóa mềm.</p><p>Phiên hoàn thành giao kỳ: {data.study.completed_sessions} · Thiếu interval hợp lệ: {data.study.excluded_sessions}</p>
        {!data.study.duration_us&&<p>Chưa có thời gian học đủ dữ liệu trong kỳ.</p>}
        <div style={{maxHeight:320,overflow:'auto'}} aria-label="Thời gian học theo ngày">{data.days.map(d=><div key={d.day} style={{margin:'10px 0'}}><time>{d.day}</time> · {durationLabel(d.duration_us)}<meter aria-label={d.day} min={0} max={Math.max(1,...data.days.map(d=>d.duration_us))} value={d.duration_us} style={{width:'100%'}}/></div>)}</div>
      </section><section><h2>Báo cáo</h2><p>Chờ hiện tại: {data.reports.pending} · Đã xử lý hiện tại: {data.reports.resolved}</p><p>Gửi trong kỳ: {data.reports.created_in_period} · Có vi phạm: {data.reports.violation_in_period} · Không vi phạm: {data.reports.no_violation_in_period}</p></section>
      <label>Xem chi tiết nhóm<select value={group} onChange={e=>{setGroup(e.target.value);setOffset(0);}}><option value="">Chọn nhóm</option>{Object.entries(labels).map(([v,label])=><option value={v} key={v}>{label}</option>)}</select></label>
      {group&&(!drill?<p role="status">Đang tải chi tiết…</p>:drill.error?<p role="alert">{drill.error}</p>:<section><h2>Chi tiết {labels[group]}</h2><p>Các đối tượng được tạo trong kỳ; hoạt động học tập theo ngày.</p><ul>{drill.items?.map((d,i)=><li key={d.id||d.day||i}>{d.label||d.day} · {d.status||durationLabel(d.duration_us||0)}{d.created_at&&<time> · {new Date(d.created_at).toLocaleString('vi-VN')}</time>}</li>)}</ul>{!drill.items?.length&&<p>Không có dữ liệu trong kỳ.</p>}<button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-50))}>Trang trước</button><button disabled={drill.next==null} onClick={()=>setOffset(drill.next!)}>Trang sau</button></section>)}
    </>}
  </>;
}
