export const MAX_DOCUMENT_BYTES = 200 * 1024 * 1024;
export const DOCUMENT_TYPES: Record<string, string> = {
  jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png",
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};
export type LearningDocument = {
  id: string; channel_id: string; uploader_id: string; original_filename: string;
  name: string; description: string | null; content_type: string; size_bytes: number;
  status: "PROCESSING" | "READY" | "FAILED"; processing_error: string | null;
  created_at: string; updated_at: string; processed_at: string | null;
  uploader_name?: string | null; can_manage?: boolean;
  checksum?: string; storage_status?: "STORED";
  derived_state?: { phase: string; retry_blocker?: string | null; full_pipeline_complete?: boolean; total_units?: number; counts?: Record<string, number>; original_read_by_model: boolean } | null;
  original_read_capability?: { representation: string; visual_model: string; semantic_coverage: string; available_independently_of_index: boolean };
};
export function documentFileError(file: Pick<File, "name" | "type" | "size">): string {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!DOCUMENT_TYPES[extension] || file.type && file.type !== DOCUMENT_TYPES[extension]) return "Chỉ hỗ trợ PDF, DOCX, JPG và PNG.";
  if (!file.size) return "Tệp không được để trống.";
  if (file.size > MAX_DOCUMENT_BYTES) return "Tệp vượt quá 200 MiB.";
  return "";
}
export const documentStatusLabel = { PROCESSING: "Đang xử lý", READY: "Sẵn sàng cho AI", FAILED: "Xử lý thất bại" };

export function documentProcessingError(code: string | null): string {
  const [category, page] = (code ?? "").split(":page=");
  const errors: Record<string, string> = {
    DERIVED_PARTIAL: "Chỉ mục mới có một phần; có đơn vị cần đọc bản gốc hoặc đọc hình theo nhu cầu.",
    DERIVED_PROCESSING_FAILED: "Xử lý chỉ mục gặp lỗi; có thể thử lại, bản gốc không bị thay đổi.",
    RAG_NOT_CONFIGURED: "Chưa cấu hình mô hình lập chỉ mục E5.",
    PDF_NO_TEXT: "PDF không có nội dung chữ hoặc hình đọc được.",
    DOCUMENT_NO_TEXT: "Tài liệu không có nội dung đọc được.",
    DOCUMENT_PASSWORD_PROTECTED: "PDF có mật khẩu. Hãy cung cấp bản đã mở khóa để xử lý.",
    DOCUMENT_VISION_DISABLED: "Chức năng OCR/Vision đang tắt.",
    DOCUMENT_VISION_KEY_MISSING: "Chưa cấu hình OpenAI API key cho OCR/Vision.",
    DOCUMENT_VISION_KEY_INVALID: "API key OCR/Vision không hợp lệ.",
    DOCUMENT_VISION_MODEL_UNSUPPORTED: "Model hiện tại chưa được hỗ trợ cho OCR/Vision.",
    DOCUMENT_VISION_QUOTA: "OpenAI đã hết quota. Cần kiểm tra billing trước khi thử lại.",
    DOCUMENT_VISION_RATE_LIMIT: "OpenAI đang giới hạn yêu cầu. Hãy chờ rồi thử lại.",
    DOCUMENT_VISION_CALL_CAP: "Đã đạt giới hạn số lần gọi OCR/Vision.",
    DOCUMENT_VISION_TIMEOUT: "OCR/Vision quá thời gian chờ.",
    DOCUMENT_VISION_INCOMPLETE: "OCR/Vision chưa đọc hết trang trong giới hạn đầu ra.",
    DOCUMENT_PAGE_UNREADABLE: "Có trang không đọc rõ chữ hoặc hình; tài liệu chưa được lập chỉ mục.",
    DOCUMENT_PAGE_LIMIT: "Tài liệu vượt giới hạn số trang OCR/Vision.",
    DOCUMENT_RENDER_LIMIT: "Kích thước trang vượt giới hạn render an toàn.",
    DOCUMENT_RENDER_FAILED: "Không thể render trang PDF.",
    DOCX_IMAGES_UNSUPPORTED: "Ảnh nhúng DOCX có vị trí hoặc định dạng chưa hỗ trợ (ảnh ngoài, header/footer). Tài liệu chưa được lập chỉ mục đầy đủ.",
    DOCX_LAYOUT_UNSUPPORTED: "DOCX có bảng lồng nhau hoặc bố cục chưa được hỗ trợ đầy đủ. Hãy cung cấp bản PDF để giữ cấu trúc.",
    VECTOR_PROCESSING_FAILED: "Không thể tạo hoặc lưu vector tài liệu.",
  };
  return (page && /^\d+$/.test(page) ? `Trang ${page}: ` : "") + (errors[category] ?? "Không thể xử lý nội dung tài liệu.") + " Tệp gốc vẫn được giữ lại.";
}

export function documentPresentation(document: LearningDocument) {
  if (document.status === "PROCESSING") return { label: "Đang xử lý", description: "Hệ thống đang chuẩn bị nội dung tài liệu cho trợ lý AI." };
  if ((document.status === "READY" && !!document.derived_state && !document.derived_state.full_pipeline_complete) || document.derived_state?.phase === "PARTIAL" || document.derived_state?.retry_blocker || document.processing_error === "DERIVED_PARTIAL") return { label: "Cần xử lý bổ sung", description: "Một số nội dung chưa thể đọc tự động." };
  if (document.status === "READY") return { label: "Sẵn sàng cho AI", description: "Trợ lý AI có thể sử dụng tài liệu này." };
  return { label: "Xử lý thất bại", description: "Không thể xử lý tài liệu. Bạn có thể thử lại." };
}
