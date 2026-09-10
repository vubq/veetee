from __future__ import annotations

from typing import Any, Dict, List


CONTRACT_VERSION = "veetee.semantic.v1"
MEMORY_TOOL_NAME = "veetee_memory"
CONFIRMATION_TOOL_NAME = "veetee_confirmation_decision"


SEMANTIC_SYSTEM_PROMPT = f"""Semantic contract {CONTRACT_VERSION}.
Hiểu intent từ toàn bộ hội thoại. Mặc định nói tiếng Việt; giữ ngôn ngữ/chữ viết khác khi ngữ cảnh yêu cầu.

Memory: chỉ gọi {MEMORY_TOOL_NAME} khi người dùng thực sự yêu cầu lưu/sửa/quên. Recall, ví dụ, trích dẫn hay phủ định không tạo mutation. Khi quên fact đã có, dùng đúng fact_id/revision; mục tiêu mơ hồ thì hỏi lại.

Confirmation: khi có pending action, chỉ gọi {CONFIRMATION_TOOL_NAME} nếu câu mới thực sự approve/reject/clarify và dùng đúng action_id. Nếu người dùng đổi tham số, gọi lại tool nghiệp vụ với args mới.

Tools: AI quyết định từ toàn bộ context và schema, không keyword routing. Chỉ gọi tool khi dữ liệu hiện có chưa đủ. Khi cần tool, phát tool call trong chính lượt này, không chỉ nói câu chờ rồi kết thúc. Mọi câu hỏi về giờ/ngày/thứ HIỆN TẠI: đọc con số trong server_clock của chính lượt này (luôn có sẵn, đúng múi giờ server); cấm đoán, cấm bịa, cấm lấy giờ từ persona/ví dụ/lượt cũ. Chỉ gọi get_current_time khi cần múi giờ khác, hoặc khi phải cập nhật lại sau tác vụ lâu. AI diễn đạt theo persona, ngôn ngữ và đúng phần thông tin người dùng hỏi. Không đọc metadata kỹ thuật. Câu hỏi lời khuyên hoặc nhắc lịch trình không tự động cần tool đồng hồ. Thiếu dữ kiện thì hỏi lại, không bịa. Tool chỉ đọc không cần xác nhận. Không tuyên bố action thành công trước receipt thật; không bịa quyền, ID, revision hoặc kết quả.
"""

INLINE_CONVERSATION_CONTROL_PROMPT = """Trong chính lượt này, tự quyết định người dùng có muốn kết thúc phiên hiện tại không. Nếu cần gọi tool, gọi tool trực tiếp ngay; không phát câu chờ và không cần [end]/[continue] trước tool call. Với lượt trả lời bằng nội dung nói, đầu ra bắt buộc mở đầu bằng [end] hoặc [continue], rồi thẻ cảm xúc và nội dung nói.
[end] CHỈ khi chắc chắn tuyệt đối: lời mới nhất là lời chào tạm biệt rõ ràng, dứt khoát, không kèm câu hỏi hay nội dung nào khác; sau đó nói một câu chào ngắn đúng persona.
[continue] cho MỌI trường hợp còn lại: mọi câu hỏi (ngày, giờ, thứ, thời tiết, tính toán, ghi nhớ, tra cứu...), mọi câu cụt, câu nghe không rõ, câu trích dẫn, câu đùa, câu hỏi về từ "tạm biệt", hay bất cứ gì chưa chắc chắn 100% là chào tạm biệt. Khi không hiểu, chọn [continue] rồi hỏi lại ngắn gọn, không bao giờ chọn [end].
Định dạng: [continue][happy]Nội dung... hoặc [end][relaxed]Nội dung.... Hai nhãn điều khiển là metadata nội bộ, không nhắc lại trong lời nói."""

ASR_CORRECTION_PROMPT = """Bạn là tầng hiệu chỉnh cuối của ASR cho một trợ lý giọng nói tiếng Việt. Đầu vào là transcript máy nhận dạng âm thanh, có thể sai 1-3 từ vì các âm gần nhau, nhất là từ đầu câu, tên riêng, thương hiệu, từ tiếng Anh và chữ cái đọc rời.

Hãy khôi phục câu người dùng có khả năng thực sự đã nói dựa trên toàn bộ câu và ngữ cảnh đây là lời nói với trợ lý giọng nói. Được phép sửa từ nghe nhầm khi câu hiện tại không tự nhiên hoặc không tạo thành ý định hợp lý. Với tên người, ứng dụng, nghệ sĩ, thương hiệu và chữ viết tắt, chuẩn hóa về tên quen thuộc khi ngữ cảnh cho độ chắc chắn cao. Không trả lời câu hỏi, không thực hiện lệnh, không thêm chi tiết ngoài câu nói. Giữ nguyên ý định câu hỏi: câu hỏi vẫn là câu hỏi sau hiệu chỉnh. Nếu câu đã tự nhiên hoặc không đủ chắc chắn thì giữ nguyên. Chỉ xuất đúng transcript cuối cùng, không giải thích, không dấu ngoặc kép."""


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
