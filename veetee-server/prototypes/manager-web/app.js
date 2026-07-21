const pageNames = {
  overview: 'Tổng quan',
  devices: 'Thiết bị',
  agents: 'Trợ lý AI',
  providers: 'Providers',
  lab: 'Realtime Lab',
  mcp: 'MCP tools',
  ota: 'OTA & releases'
};

const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];

function showPage(page) {
  if (!pageNames[page]) page = 'overview';
  $$('.page').forEach(section => section.classList.toggle('active', section.dataset.page === page));
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.pageLink === page));
  $('#pageCrumb').textContent = pageNames[page];
  history.replaceState(null, '', `#${page}`);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => el.classList.remove('show'), 2800);
}

function seedWaves() {
  $$('.wave, .lab-wave').forEach(wave => {
    if (wave.children.length) return;
    for (let i = 0; i < (wave.classList.contains('lab-wave') ? 42 : 25); i += 1) {
      const bar = document.createElement('i');
      bar.style.height = `${14 + Math.round(Math.random() * 52)}px`;
      bar.style.animationDelay = `${(i % 8) * -0.14}s`;
      wave.appendChild(bar);
    }
  });
}

function openPairing() {
  $('#pairModal').hidden = false;
  const first = $('#codeInputs input');
  window.setTimeout(() => first?.focus(), 80);
}

function closePairing() {
  $('#pairModal').hidden = true;
}

function setupCodeInputs() {
  const inputs = $$('#codeInputs input');
  inputs.forEach((input, index) => {
    input.addEventListener('input', event => {
      event.target.value = event.target.value.replace(/\D/g, '').slice(0, 1);
      if (event.target.value && inputs[index + 1]) inputs[index + 1].focus();
      $('#pairSubmit').disabled = inputs.some(item => !item.value);
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Backspace' && !input.value && inputs[index - 1]) inputs[index - 1].focus();
      if (event.key === 'Enter' && !$('#pairSubmit').disabled) $('#pairSubmit').click();
    });
  });
}

function setupLab() {
  let running = false;
  let timers = [];
  const state = $('#labState');
  const toggle = $('#labToggle');
  const prompt = $('#labPrompt');
  const log = $('#eventLog');

  const events = [
    ['listen:start', 'Assistant gate mở · mode=auto', '0 ms'],
    ['input.accepted', 'Input đủ tin cậy và hướng tới robot', '188 ms'],
    ['vad.final', 'Tự kết thúc lượt nói · không cần click lần hai', '188 ms'],
    ['stt', '“Thời tiết Hà Nội hôm nay?”', '312 ms'],
    ['tool.call', 'get_weather · cache hit', '94 ms'],
    ['llm.first_token', 'Model bắt đầu stream câu trả lời', '302 ms'],
    ['tts.first_audio', 'Azure · vi-VN-HoaiMyNeural', '486 ms']
  ];

  function renderEvent([name, detail, time]) {
    if (log.querySelector('.empty-event')) log.innerHTML = '';
    const row = document.createElement('div');
    row.className = 'event-entry';
    row.innerHTML = `<i></i><div><b>${name}</b><small>${detail}</small></div><em>${time}</em>`;
    log.appendChild(row);
  }

  function stopLab(message = 'Đã ngắt phiên. Device đã về idle.') {
    timers.forEach(timer => window.clearTimeout(timer));
    timers = [];
    running = false;
    state.classList.remove('running');
    state.innerHTML = '<i></i> Sẵn sàng';
    toggle.textContent = 'Bắt đầu phiên thử';
    prompt.textContent = message;
    $('#labOrb').classList.remove('running');
  }

  toggle.addEventListener('click', () => {
    if (running) {
      stopLab();
      toast('Đã gửi abort và dừng TTS trong mô phỏng.');
      return;
    }
    running = true;
    state.classList.add('running');
    state.innerHTML = '<i></i> Đang chạy';
    toggle.textContent = 'Dừng phiên';
    prompt.textContent = 'Đang nghe… hãy nói một câu tiếng Việt.';
    log.innerHTML = '';
    events.forEach((event, index) => {
      timers.push(window.setTimeout(() => renderEvent(event), 500 + index * 690));
    });
    timers.push(window.setTimeout(() => {
      if (running) prompt.textContent = 'Đang phát câu trả lời · bấm “Ngắt AI” để chen ngang.';
    }, 3500));
  });

  $('#interruptButton').addEventListener('click', () => {
    if (!running) {
      toast('Chưa có TTS đang chạy.');
      return;
    }
    stopLab('Đã ngắt AI trong 118 ms · sẵn sàng cho turn mới.');
    renderEvent(['abort', 'User interrupt · generation invalidated', '118 ms']);
    toast('Barge-in mô phỏng: audio queue đã được clear.');
  });
}

function setupCommandPalette() {
  const palette = $('#commandPalette');
  const input = $('#commandInput');
  const open = () => { palette.hidden = false; window.setTimeout(() => input.focus(), 50); };
  const close = () => { palette.hidden = true; input.value = ''; };
  $('#commandTrigger').addEventListener('click', open);
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); open(); }
    if (event.key === 'Escape') { close(); closePairing(); }
  });
  palette.addEventListener('click', event => {
    if (event.target === palette) close();
    const page = event.target.closest('[data-command-page]')?.dataset.commandPage;
    if (page) { close(); showPage(page); }
    if (event.target.closest('[data-command-pair]')) { close(); openPairing(); }
  });
}

$$('[data-page-link]').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  showPage(link.dataset.pageLink);
}));
$$('[data-open-pair]').forEach(button => button.addEventListener('click', openPairing));
$$('[data-close-modal]').forEach(button => button.addEventListener('click', closePairing));
$('#pairModal').addEventListener('click', event => { if (event.target.id === 'pairModal') closePairing(); });
$('#pairSubmit').addEventListener('click', () => {
  closePairing();
  toast('Đã ghép Veetee Lab 04 vào trợ lý Veetee Việt.');
});
$$('.test-provider').forEach(button => button.addEventListener('click', () => {
  const name = button.closest('.provider-row').querySelector('b').textContent;
  button.textContent = '…';
  window.setTimeout(() => { button.textContent = 'Test'; toast(`${name}: health check thành công · 284 ms`); }, 700);
}));

seedWaves();
setupCodeInputs();
setupLab();
setupCommandPalette();
showPage(location.hash.slice(1) || 'overview');
