import {NextRequest} from 'next/server';
export async function GET(request:NextRequest,{params}:{params:Promise<{id:string}>}){
  const {id}=await params;const token=request.headers.get('authorization');
  if(!token)return new Response('Unauthorized',{status:401});
  if(!/^[0-9a-f-]{36}$/i.test(id))return new Response('Invalid report',{status:400});
  const base=process.env.NEXT_PUBLIC_API_BASE_URL;
  if(!base)return new Response('API unavailable',{status:503});
  const response=await fetch(`${base.replace(/\/$/,'')}/api/v1/admin/reports/${id}/evidence`,{headers:{Authorization:token},cache:'no-store',signal:AbortSignal.timeout(120000)});
  return new Response(response.body,{status:response.status,headers:{'Content-Type':response.headers.get('content-type')||'application/octet-stream','Content-Disposition':response.headers.get('content-disposition')||'attachment','Cache-Control':'private, no-store','Content-Security-Policy':'sandbox', 'X-Content-Type-Options':'nosniff'}});
}
