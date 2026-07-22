const copy = {
  vi: {
    idle: { number: "01", kicker: "SẴN SÀNG", title: "Chào bạn.", hint: "Bấm nút hoặc nói “Hey VeeTee”" },
    listening: { number: "02", kicker: "ĐANG NGHE", title: "Bạn cứ nói.", hint: "Nói tự nhiên · bấm để tạm dừng" },
    understanding: { number: "03", kicker: "ĐANG ĐÁNH GIÁ", title: "Tôi đang nghe rõ.", hint: "Phân biệt lời nói và âm thanh nền" },
    thinking: { number: "04", kicker: "ĐANG XỬ LÝ", title: "Một chút nhé.", hint: "AI có thể gọi công cụ khi cần" },
    speaking: { number: "05", kicker: "ĐANG TRẢ LỜI", title: "Tôi đang nói.", hint: "Bấm nút để ngắt ngay" },
    stopping: { number: "06", kicker: "ĐÃ NGẮT", title: "Tôi đang nghe lại.", hint: "Lượt trước đã được hủy an toàn" },
    pairing: { number: "07", kicker: "GHÉP THIẾT BỊ", title: "Nhập mã này.", hint: "Mở Manager Web · mã hết hạn sau ít phút" },
    pairingLost: { number: "08", kicker: "MẤT GHÉP NỐI", title: "Cần kết nối lại.", hint: "Giữ nút 5 giây để bắt đầu phục hồi" },
    wifiSetup: { number: "09", kicker: "CÀI ĐẶT WI-FI", title: "Kết nối với VeeTee.", hint: "Mở 192.168.4.1 trên điện thoại" },
    boot: { number: "00", kicker: "KHỞI ĐỘNG", title: "VeeTee đang thức dậy.", hint: "Đang kiểm tra âm thanh, mạng và tài nguyên" },
    goodbye: { number: "10", kicker: "KẾT THÚC", title: "Hẹn gặp lại.", hint: "Trở về chế độ chờ tiết kiệm tài nguyên" },
  },
  en: {
    idle: { number: "01", kicker: "READY", title: "Hello there.", hint: "Press the button or say “Hey VeeTee”" },
    listening: { number: "02", kicker: "LISTENING", title: "Go ahead.", hint: "Speak naturally · press to pause" },
    understanding: { number: "03", kicker: "EVALUATING", title: "I can hear you.", hint: "Separating speech from background audio" },
    thinking: { number: "04", kicker: "WORKING", title: "One moment.", hint: "AI may call a tool when needed" },
    speaking: { number: "05", kicker: "RESPONDING", title: "I’m speaking.", hint: "Press the button to interrupt" },
    stopping: { number: "06", kicker: "INTERRUPTED", title: "I’m listening again.", hint: "The previous turn was cancelled safely" },
    pairing: { number: "07", kicker: "PAIR DEVICE", title: "Enter this code.", hint: "Open Manager Web · code expires shortly" },
    pairingLost: { number: "08", kicker: "PAIRING LOST", title: "Let’s reconnect.", hint: "Hold the button for 5 seconds to recover" },
    wifiSetup: { number: "09", kicker: "WI-FI SETUP", title: "Connect to VeeTee.", hint: "Open 192.168.4.1 on your phone" },
    boot: { number: "00", kicker: "STARTING", title: "VeeTee is waking up.", hint: "Checking audio, network and resources" },
    goodbye: { number: "10", kicker: "SESSION ENDED", title: "See you soon.", hint: "Returning to low-resource standby" },
  },
};

const flow = ["idle", "listening", "understanding", "thinking", "speaking", "stopping", "listening", "goodbye", "idle"];
const screens = [...document.querySelectorAll("[data-device-screen]")];
const stateButtons = [...document.querySelectorAll("[data-state]")].filter((element) => element.tagName === "BUTTON");
const languageButton = document.querySelector("#languageToggle");
const autoPlayButton = document.querySelector("#autoPlay");

let state = "idle";
let language = "vi";
let autoPlayTimer;
let flowIndex = 0;

function render(nextState) {
  state = nextState;
  const content = copy[language][state];

  screens.forEach((screen) => {
    screen.dataset.state = state;
    screen.querySelectorAll("[data-state-kicker]").forEach((element) => { element.textContent = content.kicker; });
    screen.querySelectorAll("[data-state-title]").forEach((element) => { element.textContent = content.title; });
    screen.querySelectorAll("[data-state-hint]").forEach((element) => { element.textContent = content.hint; });
    const number = screen.querySelector(".state-number");
    const monoIndex = screen.querySelector(".mono-index");
    if (number) number.textContent = content.number;
    if (monoIndex) monoIndex.textContent = content.number;
  });

  stateButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.state === state);
  });
}

function stopAutoPlay() {
  window.clearInterval(autoPlayTimer);
  autoPlayTimer = undefined;
  autoPlayButton.classList.remove("running");
  autoPlayButton.lastChild.textContent = " Chạy luồng";
}

function startAutoPlay() {
  flowIndex = Math.max(0, flow.indexOf(state));
  autoPlayButton.classList.add("running");
  autoPlayButton.lastChild.textContent = " Dừng luồng";
  autoPlayTimer = window.setInterval(() => {
    flowIndex = (flowIndex + 1) % flow.length;
    render(flow[flowIndex]);
  }, 2300);
}

stateButtons.forEach((button) => {
  button.addEventListener("click", () => {
    stopAutoPlay();
    render(button.dataset.state);
  });
});

languageButton.addEventListener("click", () => {
  language = language === "vi" ? "en" : "vi";
  languageButton.innerHTML = language === "vi" ? "VI <span>/ EN</span>" : "<span>VI /</span> EN";
  document.documentElement.lang = language;
  render(state);
});

autoPlayButton.addEventListener("click", () => {
  if (autoPlayTimer) stopAutoPlay();
  else startAutoPlay();
});

render(state);
