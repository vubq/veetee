from __future__ import annotations

from typing import Any, Dict, List


CONTRACT_VERSION = "veetee.semantic.v1"
MEMORY_TOOL_NAME = "veetee_memory"
CONFIRMATION_TOOL_NAME = "veetee_confirmation_decision"


SEMANTIC_SYSTEM_PROMPT = f"""Semantic contract {CONTRACT_VERSION}.
Hiểu intent từ toàn bộ hội thoại. Mặc định nói tiếng Việt; giữ ngôn ngữ/chữ viết khác khi ngữ cảnh yêu cầu.

Memory: chỉ gọi {MEMORY_TOOL_NAME} khi người dùng thực sự yêu cầu lưu/sửa/quên. Recall, ví dụ, trích dẫn hay phủ định không tạo mutation. Khi quên fact đã có, dùng đúng fact_id/revision; mục tiêu mơ hồ thì hỏi lại.

Confirmation: khi có pending action, chỉ gọi {CONFIRMATION_TOOL_NAME} nếu câu mới thực sự approve/reject/clarify và dùng đúng action_id. Nếu người dùng đổi tham số, gọi lại tool nghiệp vụ với args mới.

Tools: AI quyết định từ toàn bộ context và schema, không keyword routing. Chỉ gọi tool khi dữ liệu hiện có chưa đủ. Khi cần tool, phát tool call trong chính lượt này, không chỉ nói câu chờ rồi kết thúc. Với ngày/giờ/thứ hiện tại, dùng server_clock của lượt hiện tại nếu đúng múi giờ và đủ dữ liệu; chỉ gọi get_current_time khi cần múi giờ khác hoặc cập nhật thời gian sau tác vụ lâu. Không dùng giờ mẫu trong persona hay snapshot/receipt của lượt cũ làm giờ hiện tại. AI diễn đạt theo persona, ngôn ngữ và đúng phần thông tin người dùng hỏi. Không đọc metadata kỹ thuật. Câu hỏi lời khuyên hoặc nhắc lịch trình không tự động cần tool đồng hồ. Thiếu dữ kiện thì hỏi lại, không bịa. Tool chỉ đọc không cần xác nhận. Không tuyên bố action thành công trước receipt thật; không bịa quyền, ID, revision hoặc kết quả.
"""


def semantic_tools(*, memory_enabled: bool, pending_action: bool) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    if memory_enabled:
        tools.append({
            "type": "function",
            "function": {
                "name": MEMORY_TOOL_NAME,
                "description": (
                    "Đề xuất mutation memory khi và chỉ khi người dùng thực sự yêu cầu lưu/sửa/quên. "
                    "Recall là chat bình thường, không gọi tool này."
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
