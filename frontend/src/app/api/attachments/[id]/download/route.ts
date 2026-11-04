// Download through backend authorization on every request. Never expose a storage grant.
export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  const authorization = request.headers.get("authorization");
  const failure = (status: number, code: string, message: string) => Response.json({ error: { code, message, fields: null } }, { status });
  if (!authorization) return failure(401, "AUTHENTICATION_REQUIRED", "Yêu cầu đăng nhập");
  if (!/^[0-9a-f-]{36}$/i.test(id)) return failure(422, "VALIDATION_ERROR", "Tệp không hợp lệ");
  if (!base) return failure(503, "CONFIGURATION_ERROR", "Chưa cấu hình địa chỉ API");
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/api/v1/attachments/${id}/download`, {
      headers: { Authorization: authorization }, redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) return new Response(response.body, { status: response.status === 307 ? 502 : response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
    const headers = new Headers({ "Content-Type": response.headers.get("content-type") || "application/octet-stream", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy":"sandbox" });
    const disposition = response.headers.get("content-disposition");
    if (disposition) headers.set("Content-Disposition", disposition);
    return new Response(response.body, { headers });
  } catch { return failure(502, "ATTACHMENT_DOWNLOAD_FAILED", "Không thể tải tệp. Vui lòng thử lại."); }
}
