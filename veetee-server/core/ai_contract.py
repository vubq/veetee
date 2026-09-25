from __future__ import annotations

from typing import Any, Dict, List


CONTRACT_VERSION = "veetee.semantic.v1"
MEMORY_TOOL_NAME = "veetee_memory"
CONFIRMATION_TOOL_NAME = "veetee_confirmation_decision"
NO_ACTION_TOOL_NAME = "veetee_no_action"


SEMANTIC_SYSTEM_PROMPT = f"""Semantic contract {CONTRACT_VERSION}.
Hiểu intent từ toàn bộ hội thoại; mặc định tiếng Việt, giữ ngôn ngữ khác khi ngữ cảnh yêu cầu.

Memory: chỉ gọi {MEMORY_TOOL_NAME} cho fact cá nhân ổn định/hữu ích lâu dài hoặc khi user yêu cầu lưu-sửa-quên. Không lưu suy diễn, trạng thái thoáng qua, giả định/trích dẫn/phủ định; recall không mutation. Fact mới: upsert+value, bỏ fact_id/revision. Sửa/quên fact cũ: dùng đúng fact_id/revision server cấp; mơ hồ thì hỏi lại.

Confirmation: chỉ gọi {CONFIRMATION_TOOL_NAME} cho pending action khi lời mới thật sự approve/reject/clarify và dùng đúng action_id; đổi tham số thì gọi tool nghiệp vụ mới.

Tools: chọn từ context+schema, không keyword routing. Cần tool thì tool call phải trước speech; hành động thật bắt buộc có tool và receipt, không bịa kết quả/quyền/ID/revision. Tên tool/schema/args/call id/receipt/status/lỗi là metadata nội bộ: tuyệt đối không đọc/hiển thị. Tool thiếu/sai args nhưng user đã cho đủ dữ kiện thì tự gọi lại tool; chỉ hỏi khi thật sự thiếu. Follow-up slot filling: nếu trợ lý vừa hỏi dữ kiện còn thiếu và user trả lời bằng một cụm ngắn, dùng ngay làm tham số; không lặp lại cùng câu hỏi.

Time: server_clock của lượt là authoritative cho giờ/ngày/thứ local. Dùng trực tiếp; không gọi tool cho giờ/ngày local. Nếu hỏi timezone khác mà chưa có dữ liệu thì không bịa. Trả đúng phần được hỏi, không tự thêm giờ/ngày/thứ khác.

Luôn theo persona; thiếu dữ kiện thì hỏi ngắn.
"""

END_INTENT_VERIFICATION_PROMPT = """Xác minh lifecycle của lời USER mới nhất trong ngữ cảnh hội thoại.
Chỉ trả đúng END hoặc CONTINUE.
END chỉ khi USER thật sự muốn kết thúc chính phiên hiện tại và không còn câu hỏi/tác vụ khác.
CONTINUE nếu USER đang hỏi/giải thích/trích dẫn/nhắc tới lời chào, nói giả định, kết thúc việc khác, còn yêu cầu khác, hoặc ý định mơ hồ.
Khi không chắc chắn, trả CONTINUE."""

INLINE_CONVERSATION_CONTROL_PROMPT = """Lifecycle là metadata nội bộ.
Cần tool: gọi tool trước speech; không cần marker trước tool call.
Trả lời bằng lời: [continue][emotion]Nội dung... hoặc [end][emotion]Nội dung....
[end] chỉ khi lời mới nhất rõ ràng muốn kết thúc CHÍNH phiên hiện tại và không còn câu hỏi/tác vụ; chào ngắn, tự nhiên, trọn nghĩa; cấm câu dang dở.
[continue] cho mọi trường hợp khác: mơ hồ, phủ định ("mình không nói tạm biệt"), trích dẫn, giả định hoặc chỉ kết thúc tác vụ; không hiểu thì hỏi lại. Xong tác vụ không đồng nghĩa đóng phiên.
Không đọc/giải thích marker."""

RECOVERY_MESSAGE_PROMPT = """Tạo đúng một câu hoàn chỉnh để trợ lý giọng nói dùng khi một lượt xử lý không hoàn tất.
Nói tự nhiên theo personality hiện tại, không đọc lỗi kỹ thuật, không nhắc hệ thống/API, không thêm nhãn và không dùng câu chăm sóc khách hàng sáo rỗng.
Câu phải tự đứng độc lập, có ý nghĩa trọn vẹn, 4-14 từ; cấm mảnh câu kiểu "Có vẻ", "Hình như", "Xin lỗi" đứng một mình. Mặc định dùng tiếng Việt."""

IDLE_FAREWELL_SYSTEM_PROMPT = """Sự kiện hệ thống: hội thoại đã hết thời gian chờ và phiên sẽ đóng ngay sau câu tiếp theo.
Đây là một lượt lifecycle RIÊNG, không phải lượt trả lời câu hỏi trước. Lịch sử hỏi/đáp cũ cố ý không được cung cấp; tuyệt đối không nhắc lại, hoàn tất hay diễn giải câu trả lời trước đó.
Chỉ tạo đúng một câu chào tạm biệt ngắn, tự nhiên, đúng persona. Câu phải mang nghĩa kết thúc rõ ràng; cấm hỏi, cấm nói đang chờ/đang ở đây, cấm mời người dùng nói tiếp và cấm thêm thông tin khác."""

IDLE_FAREWELL_USER_PROMPT = "Hãy nói một câu chào tạm biệt ngắn để kết thúc phiên ngay bây giờ."

IDLE_FAREWELL_RETRY_PROMPT = """Câu vừa tạo không dùng được vì trống, sai định dạng hoặc lặp lại nội dung của lượt trước.
Tự tạo lại đúng một câu kết thúc phiên ngắn, tự nhiên, đúng persona; không nhắc lại nội dung trước."""

ASR_CORRECTION_PROMPT = """Bạn là tầng hiệu chỉnh cuối của ASR cho một trợ lý giọng nói tiếng Việt. Đầu vào là transcript máy nhận dạng âm thanh, có thể sai 1-3 từ vì các âm gần nhau, nhất là từ đầu câu, tên riêng, thương hiệu, từ tiếng Anh và chữ cái đọc rời.

Hãy khôi phục câu người dùng có khả năng thực sự đã nói dựa trên toàn bộ câu và ngữ cảnh đây là lời nói với trợ lý giọng nói. Được phép sửa từ nghe nhầm khi câu hiện tại không tự nhiên hoặc không tạo thành ý định hợp lý. Với tên người, ứng dụng, nghệ sĩ, thương hiệu và chữ viết tắt, chuẩn hóa về tên quen thuộc khi ngữ cảnh cho độ chắc chắn cao. Không trả lời câu hỏi, không thực hiện lệnh, không thêm chi tiết ngoài câu nói. Giữ nguyên ý định câu hỏi: câu hỏi vẫn là câu hỏi sau hiệu chỉnh. Nếu câu đã tự nhiên hoặc không đủ chắc chắn thì giữ nguyên. Chỉ xuất đúng transcript cuối cùng, không giải thích, không dấu ngoặc kép."""


def semantic_tools(
    *,
    memory_enabled: bool,
    pending_action: bool,
    include_no_action: bool = False,
) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    if include_no_action:
        tools.append({
            "type": "function",
            "function": {
                "name": NO_ACTION_TOOL_NAME,
                "description": (
                    "Marker ngữ nghĩa không có side effect. Chỉ chọn khi lời mới của người dùng "
                    "không yêu cầu bất kỳ action/tool nào đang có. Nếu họ muốn phát/đổi bài, "
                    "điều khiển nhạc, tính toán, memory hoặc action khác thì phải chọn tool tương ứng, "
                    "không chọn marker này."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        })
    if memory_enabled:
        tools.append({
            "type": "function",
            "function": {
                "name": MEMORY_TOOL_NAME,
                "description": (
                    "Ghi/sửa/quên memory cá nhân ổn định; recall không gọi tool."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["upsert", "forget", "forget_all"]},
                        "value": {"type": "string", "maxLength": 500},
                        "fact_id": {"type": "string", "maxLength": 96},
                        "revision": {"type": "integer", "minimum": 1},
                        "evidence": {"type": "string", "maxLength": 500},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        })
    if pending_action:
        tools.append({
            "type": "function",
            "function": {
                "name": CONFIRMATION_TOOL_NAME,
                "description": "Quyết định ngữ nghĩa cho pending action hiện tại dựa trên toàn bộ ngữ cảnh.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action_id": {"type": "string", "minLength": 1, "maxLength": 160},
                        "decision": {"type": "string", "enum": ["approve", "reject", "clarify"]},
                    },
                    "required": ["action_id", "decision"],
                    "additionalProperties": False,
                },
            },
        })
    return tools
