import type { ApiError } from "./types";
export class ApiFailure extends Error {
  constructor(public status: number, public error: ApiError) { super(error.message); }
}
export interface AuthBridge { getToken: () => string | null; refresh: () => Promise<string>; clear: () => void }
export function createApiClient(auth: AuthBridge) {
  return async function request<T>(path: string, options: { method?: string; body?: unknown; protected?: boolean; download?: boolean; downloadFilename?: string; downloadLink?: boolean; timeoutMs?: number } = {}): Promise<T> {
    const base = process.env.NEXT_PUBLIC_API_BASE_URL;
    if (!base) throw new ApiFailure(0, { code: "CONFIGURATION_ERROR", message: "Chưa cấu hình địa chỉ API.", fields: null });
    async function send(token: string | null): Promise<T> {
      let response: Response;
      try {
        const multipart = options.body instanceof FormData;
        response = await fetch(options.download || options.downloadLink ? `/api${path}` : `${base!.replace(/\/$/, "")}/api/v1${path}`, {
          method: options.method ?? "GET", credentials: "include", cache: "no-store",
          headers: { ...(options.body && !multipart ? { "Content-Type": "application/json" } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
          body: multipart ? options.body as FormData : options.body ? JSON.stringify(options.body) : undefined,
          signal: AbortSignal.timeout(options.timeoutMs ?? (multipart || options.download ? 120000 : 15000)),
        });
      } catch { throw new ApiFailure(0, { code: "NETWORK_ERROR", message: "Không thể kết nối máy chủ. Vui lòng thử lại.", fields: null }); }
      if (!response.ok) {
        let error: ApiError = { code: "UNKNOWN_ERROR", message: "Yêu cầu thất bại. Vui lòng thử lại.", fields: null };
        try {
          const payload: { error?: ApiError } = await response.json();
          if (payload.error && typeof payload.error.code === "string") error = payload.error;
        } catch { /* Non-JSON server/proxy response. */ }
        throw new ApiFailure(response.status, error);
      }
      if (response.status === 204) return undefined as T;
      if (options.download) {
        let blob: Blob;
        try { blob = await response.blob(); }
        catch (error) {
          const reason = error instanceof Error ? `${error.name}: ${error.message}` : "Không đọc được response";
          throw new ApiFailure(0, { code: "ATTACHMENT_BODY_FAILED", message: `Không đọc được dữ liệu tệp (${reason}).`, fields: null });
        }
        const disposition = response.headers.get("content-disposition") || "";
        const extended = /filename\*\s*=\s*UTF-8'[^']*'([^;]+)/i.exec(disposition);
        const plain = /filename\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;]+))/i.exec(disposition);
        let filename = plain?.[1]?.replace(/\\(.)/g, "$1") || plain?.[2]?.trim() || options.downloadFilename || "download";
        if (extended) { try { filename = decodeURIComponent(extended[1].trim()); } catch { /* Keep the fallback for malformed encoding. */ } }
        filename = filename.split(/[\\/]/).pop()?.replace(/\p{Cc}/gu, "") || options.downloadFilename || "download";
        return new File([blob], filename, { type: blob.type }) as T;
      }
      const payload: { data: T } = await response.json();
      return payload.data;
    }
    const token = options.protected ? auth.getToken() : null;
    try { return await send(token); }
    catch (error) {
      if (!options.protected || !(error instanceof ApiFailure) || error.status !== 401) throw error;
      // Refresh uses an unprotected request, so it can never recurse here.
      let replacement: string;
      try { replacement = auth.getToken() !== token && auth.getToken() ? auth.getToken()! : await auth.refresh(); }
      catch (refreshError) { auth.clear(); throw refreshError; }
      try { return await send(replacement); }
      catch (retryError) {
        if (retryError instanceof ApiFailure && retryError.status === 401) auth.clear();
        throw retryError;
      }
    }
  };
}
