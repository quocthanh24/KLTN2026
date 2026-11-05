"use client";
import { useState } from "react";
import { createApiClient } from "@/lib/api";
import { chatAuth, clearAuth } from "@/features/auth/store";
const api=createApiClient({...chatAuth,clear:clearAuth});
export function ReportButton({targetType,targetId}:{targetType:"MESSAGE"|"DOCUMENT";targetId:string}) {
  const [open,setOpen]=useState(false),[reason,setReason]=useState(""),[description,setDescription]=useState("");
  const [pending,setPending]=useState(false),[notice,setNotice]=useState("");
  return <><button type="button" onClick={()=>{setOpen(true);setNotice("");}}>Báo cáo vi phạm</button>
    {open&&<div role="dialog" aria-modal="true" aria-label="Báo cáo vi phạm"><form onSubmit={async e=>{
      e.preventDefault();if(pending||!reason)return;setPending(true);setNotice("");
      try{await api('/violation-reports',{method:'POST',protected:true,body:{target_type:targetType,target_id:targetId,reason,description:description.trim()||null}});setOpen(false);setNotice('Đã gửi báo cáo. Báo cáo đang chờ xử lý.');}
      catch(error){setNotice(error instanceof Error?error.message:'Không thể gửi báo cáo.');}finally{setPending(false);}
    }}><fieldset disabled={pending}><label>Lý do báo cáo<select required value={reason} onChange={e=>setReason(e.target.value)}><option value="">Chọn lý do</option><option value="SPAM">Nội dung rác</option><option value="ABUSE">Nội dung không phù hợp</option><option value="COPYRIGHT">Vi phạm bản quyền</option><option value="OTHER">Lý do khác</option></select></label>
      <label>Mô tả báo cáo<textarea maxLength={2000} value={description} onChange={e=>setDescription(e.target.value)} /></label><button disabled={!reason}>{pending?'Đang gửi…':'Gửi báo cáo'}</button><button type="button" onClick={()=>setOpen(false)}>Hủy báo cáo</button>
    </fieldset></form></div>}{notice&&<p role="status">{notice}</p>}</>;
}
