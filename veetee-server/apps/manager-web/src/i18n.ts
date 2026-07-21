import { createI18n } from "vue-i18n";

const viVN = {
  boot: "Đang kiểm tra phiên quản trị…",
  login: {
    eyebrow: "LAN-FIRST CONTROL ROOM",
    headingLead: "Một nơi yên tĩnh để vận hành",
    headingAccent: "robot biết lắng nghe.",
    description:
      "Quản lý device, agent, local speech model, 9Router và MCP trên chính máy chủ của bạn. Không cần domain để bắt đầu.",
    workspaceAccess: "WORKSPACE ACCESS",
    title: "Đăng nhập Veetee.",
    security: "Access token ngắn hạn; refresh token được giữ trong cookie HttpOnly.",
    email: "Email",
    password: "Mật khẩu",
    workspace: "Workspace slug",
    workspaceHint: "tùy chọn nếu tài khoản chỉ có một workspace",
    submit: "Vào control room →",
    submitting: "Đang xác thực…",
    sourceNotice: "Thông tin đăng nhập được tạo từ bootstrap Manager API, không lưu trong source.",
    connectionError: "Không thể kết nối Manager API.",
  },
};

const enUS = {
  boot: "Checking the management session…",
  login: {
    eyebrow: "LAN-FIRST CONTROL ROOM",
    headingLead: "A quiet place to operate",
    headingAccent: "a robot that knows how to listen.",
    description:
      "Manage devices, agents, local speech models, 9Router and MCP on your own server. No domain is required to start.",
    workspaceAccess: "WORKSPACE ACCESS",
    title: "Sign in to Veetee.",
    security: "Short-lived access token; the refresh token stays in an HttpOnly cookie.",
    email: "Email",
    password: "Password",
    workspace: "Workspace slug",
    workspaceHint: "optional when the account belongs to one workspace",
    submit: "Enter the control room →",
    submitting: "Authenticating…",
    sourceNotice: "Credentials come from Manager API bootstrap and are never stored in source.",
    connectionError: "Unable to reach Manager API.",
  },
};

export const i18n = createI18n({
  legacy: false,
  locale: "vi-VN",
  fallbackLocale: "en-US",
  messages: { "vi-VN": viVN, "en-US": enUS },
});
