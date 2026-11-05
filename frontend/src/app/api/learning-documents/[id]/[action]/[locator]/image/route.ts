// Private image relay. No arbitrary upstream URL, redirect or public storage link.
export async function GET(request: Request, { params }: { params: Promise<{ id: string; action: string; locator: string }> }) {
  const { id, action: kind, locator } = await params;
  const authorization = request.headers.get("authorization");
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  const fail = (status: number, message: string) => Response.json({ error: { code: "DOCUMENT_IMAGE_FAILED", message, fields: null } }, { status, headers: { "Cache-Control": "private, no-store" } });
  if (!authorization) return fail(401, "Yêu cầu đăng nhập");
  if (!/^[0-9a-f-]{36}$/i.test(id)) return fail(404, "Không tìm thấy tài liệu");
  if (kind === "regions" ? !/^rId\d{1,6}$/.test(locator) : kind !== "pages" || !/^\d{1,5}$/.test(locator) || Number(locator) < 1 || Number(locator) > 10000) return fail(404, "Vùng ảnh không hợp lệ");
  const channel = new URL(request.url).searchParams.get("channel_id");
  const revision = new URL(request.url).searchParams.get("revision");
  if (channel && !/^[0-9a-f-]{36}$/i.test(channel)) return fail(422, "Kênh không hợp lệ");
  if (revision && !/^[0-9a-f]{64}$/.test(revision)) return fail(422, "Revision không hợp lệ");
  const query = new URLSearchParams();
  if (channel) query.set("channel_id", channel);
  if (revision) query.set("revision", revision);
  if (!base) return fail(503, "Chưa cấu hình API");
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/api/v1/learning-documents/${id}/${kind}/${locator}/image${query.size ? `?${query}` : ""}`, {
      headers: { Authorization: authorization }, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(35000),
    });
    return new Response(response.body, { status: response.status, headers: {
      "Content-Type": response.headers.get("content-type") ?? "application/octet-stream",
      "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
    } });
  } catch { return fail(503, "Không thể mở ảnh nguồn. Vui lòng thử lại."); }
}
