"""User-facing causes derived from actual source/tool/provider outcomes."""


def source_failure_message(state):
    codes=set(state.get('tool_errors',[]))|set(state.get('reason_codes',[]))
    terminal=state.get('terminal_error_code')
    if terminal in {'AI_PROVIDER_INCOMPLETE','AI_PROVIDER_NETWORK','AI_PROVIDER_ERROR','AI_PROVIDER_INVALID_RESPONSE'}:
        return 'Phản hồi từ dịch vụ AI bị gián đoạn hoặc chưa đầy đủ. Phần đã kiểm chứng được giữ lại; bạn có thể thử tiếp tục.'
    if terminal in {'AI_API_KEY_MISSING','AI_MODEL_MISSING','AI_API_KEY_INVALID','AI_MODEL_UNAVAILABLE','AI_PROVIDER_CONFIGURATION'}:
        return 'Trợ lý AI chưa thể hoạt động với cấu hình hiện tại. Quản trị viên cần kiểm tra cấu hình dịch vụ AI.'
    if 'BUDGET' in codes:
        return 'Lượt xử lý đã dừng do hạn mức dịch vụ AI. Phần đã kiểm chứng được giữ lại.'
    if 'MODEL_TIMEOUT' in codes:
        return 'Dịch vụ AI chưa phản hồi xong trong thời gian cho phép. Phần đã kiểm chứng được giữ lại; bạn có thể tiếp tục nếu tác vụ còn hỗ trợ.'
    if codes & {'AI_V2_ORIGINAL_CAPABILITY','AI_V2_ORIGINAL_LAYOUT','DOCUMENT_RENDER_FAILED'}:
        return 'Trang này chứa nội dung hình ảnh mà hệ thống hiện chưa đọc được.'
    if codes & {'DOCUMENT_NOT_FOUND','AI_V2_SOURCE_UNAVAILABLE','AI_SOURCE_UNAVAILABLE','DOCUMENT_FILE_UNAVAILABLE','DOCUMENT_STORAGE_FAILED','AI_V2_SOURCE_CHANGED'}:
        return 'Không thể truy cập nội dung tài liệu lúc này. Vui lòng thử lại.'
    if codes & {'AI_PAGE_NOT_FOUND','AI_V2_ARGUMENT_INVALID','AI_V2_ORIGINAL_UNSUPPORTED','AI_V2_SOURCE_SELECTION_MISMATCH'}:
        return 'Mình chưa xác định được trang bạn yêu cầu trong tài liệu. Hãy kiểm tra tài liệu đã chọn và cho biết số trang PDF vật lý hay số trang in.'
    if codes & {'MODEL_TIMEOUT','AI_V2_IMAGE_LIMIT','AI_V2_TOOL_LIMIT','INCOMPLETE'}:
        return 'Lượt xử lý đã dừng trước khi hoàn tất. Phần đã kiểm chứng được giữ lại; bạn có thể tiếp tục nếu tác vụ còn hỗ trợ.'
    if 'REVIEW_PROTOCOL_ERROR' in codes:
        return 'Bước kiểm chứng câu trả lời chưa hoàn tất. Bạn có thể gửi lại câu hỏi.'
    return 'Nội dung trả lời chưa được nguồn tài liệu xác nhận đầy đủ. Bạn có thể yêu cầu đọc lại trang cụ thể.'
