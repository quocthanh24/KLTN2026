// Relay only authenticated application responses, never a public MinIO URL.
export async function GET(request: Request, { params }: { params: Promise<{ id: string; action: string }> }) {
  const { id, action } = await params;
  const authorization = request.headers.get("authorization");
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  const failure = (status: number, message: string) => Response.json({ error: { code: "DOCUMENT_ACCESS_FAILED", message, fields: null } }, { status, headers: { "Cache-Control": "no-store" } });
  if (!authorization) return failure(401, "Yêu cầu đăng nhập");
  if (!/^[0-9a-f-]{36}$/i.test(id) || !["preview", "download", "image"].includes(action)) return failure(404, "Không tìm thấy tài liệu");
  if (!base) return failure(503, "Chưa cấu hình API");
  const query = new URL(request.url).searchParams;
  const channel = query.get("channel_id");
  if (channel && !/^[0-9a-f-]{36}$/i.test(channel)) return failure(422, "Kênh không hợp lệ");
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/api/v1/learning-documents/${id}/${action}${channel ? `?channel_id=${encodeURIComponent(channel)}` : ""}`, {
      headers: { Authorization: authorization }, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(120000),
    });
    const headers = new Headers({ "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" });
    for (const name of ["content-type", "content-disposition", "content-security-policy"]) {
      const value = response.headers.get(name); if (value) headers.set(name, value);
    }
    return new Response(response.body, { status: response.status, headers });
  } catch { return failure(503, "Không thể tải nội dung tài liệu. Vui lòng thử lại"); }
}
