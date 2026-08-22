import { expect, test, type Page } from '@playwright/test'

import { createState, installMockApi, type MockApiState } from './mock-api'

const email = 'owner@example.test'
const password = 'a-test-password-long-enough'

function failOnBrowserErrors(page: Page) {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource:')) {
      errors.push(`console.error: ${message.text()}`)
    }
  })
  return () => expect(errors, errors.join('\n')).toEqual([])
}

async function login(page: Page, state: MockApiState) {
  await installMockApi(page, state)
  await page.goto('/')
  await page.getByLabel('Email đăng nhập').fill(email)
  await page.getByLabel('Mật khẩu').fill(password)
  await page.getByRole('button', { name: 'Đăng nhập' }).click()
  await expect(page.getByRole('heading', { name: 'Trợ lý', exact: true })).toBeVisible()
}

async function expectNoHorizontalOverflow(page: Page) {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
  expect(
    scrollWidth,
    `Nội dung tràn ngang: scrollWidth ${scrollWidth}px > viewport ${clientWidth}px`,
  ).toBeLessThanOrEqual(clientWidth)
}

test('chỉ hiển thị auth screen trước đăng nhập và xử lý lỗi có thể thử lại', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({ loginStatus: 401, agents: [] })
  await installMockApi(page, state)
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Bảng điều khiển Veetee' })).toBeVisible()
  await expect(page.getByRole('navigation')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Đăng nhập' })).toBeDisabled()
  await page.getByLabel('Email đăng nhập').fill(email)
  await page.getByLabel('Mật khẩu').fill(password)
  await page.getByRole('button', { name: 'Đăng nhập' }).click()
  await expect(page.getByRole('alert')).toContainText('Thông tin đăng nhập không đúng')

  state.loginStatus = 200
  await page.getByLabel('Mật khẩu').fill(`${password}!`)
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: 'Đăng nhập' }).click()
  await expect(page.getByRole('heading', { name: 'Trợ lý', exact: true })).toBeVisible()
  assertNoErrors()
})

test('đăng xuất revoke phiên và 401 tự quay lại auth screen', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)
  await page.getByRole('button', { name: 'Menu tài khoản' }).click()
  await page.getByRole('menuitem', { name: 'Đăng xuất' }).click()
  await expect(page.getByLabel('Email đăng nhập')).toBeVisible()
  expect(state.requests.some((request) => request.path === '/api/v1/control/auth/logout')).toBe(true)

  await page.getByLabel('Email đăng nhập').fill(email)
  await page.getByLabel('Mật khẩu').fill(password)
  await page.getByRole('button', { name: 'Đăng nhập' }).click()
  await expect(page.getByRole('heading', { name: 'Trợ lý', exact: true })).toBeVisible()
  state.expireNextRequest = true
  await page.getByRole('button', { name: 'Lịch sử' }).click()
  await expect(page.getByLabel('Email đăng nhập')).toBeVisible()
  assertNoErrors()
})

test('tạo, đổi tên và xóa trợ lý đều gọi API thật', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({ agents: [] })
  await login(page, state)

  await page.getByRole('button', { name: 'Tạo trợ lý' }).click()
  await page.getByRole('menuitem', { name: 'Tạo trợ lý mới' }).click()
  await page.getByLabel('Tên trợ lý').fill('Trợ lý mới')
  await page.getByTestId('create-agent-submit').click()
  await expect(page.getByRole('heading', { name: 'Trợ lý mới', exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Thao tác' }).click()
  await page.getByRole('menuitem', { name: 'Đổi tên' }).click()
  await page.getByTestId('rename-input').fill('Trợ lý đã đổi tên')
  await page.getByTestId('rename-save').click()
  await expect(page.getByRole('heading', { name: 'Trợ lý đã đổi tên' })).toBeVisible()

  await page.getByRole('button', { name: 'Thao tác' }).click()
  await page.getByRole('menuitem', { name: 'Xóa trợ lý' }).click()
  await page.getByTestId('delete-confirm').click()
  await expect(page.getByText('Chưa có trợ lý nào')).toBeVisible()
  expect(state.agents).toEqual([])
  assertNoErrors()
})

test('cấu hình trợ lý lưu đúng payload, giữ giá trị ẩn và card cập nhật', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)

  // Giữ lời gọi catalog để quan sát trạng thái loading một cách deterministic.
  state.holdPatterns.push({ method: 'GET', path: '/api/v1/control/providers' })
  await page.getByRole('button', { name: 'Cấu hình' }).click()
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toBeVisible()
  await expect(page.getByTestId('config-catalog-loading')).toContainText('Đang tải danh sách mô hình...')
  await expect(page.getByText('lượt hội thoại tiếp theo')).toBeVisible()

  await state.heldRequests.at(-1)!.respond(200, [
    { kind: 'asr', provider_id: 'pho_whisper', models: ['mad1999/pho-whisper-small-ct2'], secret_configurable: false },
    { kind: 'llm', provider_id: 'omniroute', models: ['groq/openai/gpt-oss-120b', 'groq/qwen/qwen3.6-27b'], secret_configurable: false },
    { kind: 'tts', provider_id: 'vieneu', models: ['local'], secret_configurable: false },
  ])

  // Catalog thật được mock ở HTTP boundary: chỉ mô hình kind=llm xuất hiện trong danh sách.
  const modelTrigger = page.getByRole('combobox', { name: 'Mô hình ngôn ngữ' })
  await expect(modelTrigger).toBeVisible()
  await modelTrigger.click()
  const modelList = page.getByRole('listbox')
  await expect(modelList.getByRole('option', { name: 'groq/openai/gpt-oss-120b' })).toBeVisible()
  await expect(modelList.getByRole('option', { name: 'groq/qwen/qwen3.6-27b' })).toBeVisible()
  await expect(modelList.getByRole('option', { name: 'mad1999/pho-whisper-small-ct2' })).toHaveCount(0)
  await expect(modelList.getByRole('option', { name: 'local' })).toHaveCount(0)
  await modelList.getByRole('option', { name: 'groq/qwen/qwen3.6-27b' }).click()

  await page.getByTestId('config-role-prompt').fill('Đồng hành kiên nhẫn với trẻ nhỏ.')
  await page.getByTestId('config-personality').fill('Ấm áp, kiên nhẫn với trẻ nhỏ')
  await page.getByTestId('config-address-style').fill('Xưng chú với trẻ nhỏ')
  await page.getByRole('combobox', { name: 'Mức độ chi tiết' }).click()
  await page.getByRole('option', { name: 'Chi tiết đầy đủ' }).click()
  await page.getByTestId('config-save').click()
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toHaveCount(0)

  const put = state.requests.find((request) => request.method === 'PUT' && request.path === `/api/v1/control/agents/${state.agents[0]?.id}`)
  expect(put?.body).toMatchObject({
    name: 'Trợ lý gia đình',
    role_prompt: 'Đồng hành kiên nhẫn với trẻ nhỏ.',
    personality: 'Ấm áp, kiên nhẫn với trẻ nhỏ',
    address_style: 'Xưng chú với trẻ nhỏ',
    language: 'vi-VN',
    detail_level: 'detailed',
    response_style: 'balanced',
    model_id: 'groq/qwen/qwen3.6-27b',
    // Giá trị không hiển thị phải giữ nguyên khi PUT.
    voice_id: '',
    intent_strategy: 'function_call',
    memory_enabled: true,
    memory_min_confidence: 0.8,
    tool_policy: {},
    memory_policy: {},
    expected_version: 1,
  })
  expect(state.agents[0]?.model_id).toBe('groq/qwen/qwen3.6-27b')

  // Card cập nhật theo dữ liệu mới sau khi lưu.
  const card = page.locator('.agent-card')
  await expect(card.getByText('groq/qwen/qwen3.6-27b')).toBeVisible()
  await expect(card.getByText('Ấm áp, kiên nhẫn với trẻ nhỏ')).toBeVisible()
  assertNoErrors()
})

test('lỗi tải provider catalog cho phép thử lại và khôi phục danh sách mô hình', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({ providersStatus: 500 })
  await login(page, state)

  await page.getByRole('button', { name: 'Cấu hình' }).click()
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toBeVisible()
  await expect(page.getByTestId('config-catalog-error')).toContainText('Không tải được danh sách mô hình')
  await expect(page.getByRole('combobox', { name: 'Mô hình ngôn ngữ' })).toHaveCount(0)
  await expect(page.getByTestId('config-save')).toBeEnabled()

  state.providersStatus = 200
  await page.getByTestId('config-catalog-retry').click()
  await expect(page.getByRole('combobox', { name: 'Mô hình ngôn ngữ' })).toBeVisible()
  await page.getByRole('combobox', { name: 'Mô hình ngôn ngữ' }).click()
  await expect(page.getByRole('option', { name: 'groq/qwen/qwen3.6-27b' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  assertNoErrors()
})

test('xung đột phiên bản 409 hiển thị lỗi, tải lại dữ liệu mới và đóng', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({ updateStatus: 409 })
  await login(page, state)

  const countAgentGets = () => state.requests.filter((request) => request.method === 'GET' && request.path === '/api/v1/control/agents').length

  await page.getByRole('button', { name: 'Cấu hình' }).click()
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toBeVisible()
  await page.getByTestId('config-personality').fill('Bản nháp bị xung đột')
  await page.getByTestId('config-save').click()
  await expect(page.getByTestId('config-error')).toContainText('vừa được thay đổi ở nơi khác')
  await expect(page.getByTestId('config-reload')).toBeVisible()

  // Server chưa nhận thay đổi nào vì PUT trả 409.
  expect(state.agents[0]?.personality).toBe('')
  const getsBeforeReload = countAgentGets()

  state.updateStatus = 200
  await page.getByTestId('config-reload').click()
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toHaveCount(0)
  await expect.poll(countAgentGets).toBe(getsBeforeReload + 1)

  // Mở lại hộp thoại: form hiện giá trị trên máy chủ, không phải bản nháp cũ.
  await page.getByRole('button', { name: 'Cấu hình' }).click()
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toBeVisible()
  await expect(page.getByTestId('config-personality')).toHaveValue('')
  await expect(page.getByTestId('config-error')).toHaveCount(0)
  assertNoErrors()
})

test('không đóng hộp thoại khi PUT đang chạy và giữ metadata card sau lưu', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  const agentId = state.agents[0]!.id
  state.devices = [{ id: 'device-1', device_id: 'dev-1', agent_id: agentId, alias: '', online: true }]
  state.conversations = [{ id: 'conv-1', agent_id: agentId, device_id: 'device-1', title: 'Cuộc trò chuyện đã giữ', summary: '', locale: 'vi-VN', turn_count: 1, started_at: '2026-08-22T01:00:00Z', ended_at: null }]
  state.holdPatterns.push({ method: 'PUT', path: `/api/v1/control/agents/${state.agents[0]!.id}` })
  await login(page, state)
  await page.getByRole('button', { name: 'Cấu hình' }).click()
  await page.getByTestId('config-personality').fill('Cấu hình mới')
  await page.getByTestId('config-save').click()
  await expect(page.getByTestId('config-save')).toBeDisabled()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toBeVisible()

  await state.heldRequests.at(-1)!.respond(200, {
    ...state.agents[0],
    personality: 'Cấu hình mới',
    version: 2,
  })
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toHaveCount(0)
  const card = page.locator('.agent-card')
  await expect(card.getByText('Thiết bị (1)')).toBeVisible()
  await expect(card.getByText('Trực tuyến')).toBeVisible()
  await expect(card.getByText('Cuộc trò chuyện đã giữ')).toBeVisible()
  assertNoErrors()
})

test('hộp thoại cấu hình không tràn ngang, focus đúng, Escape đóng và không có control no-op', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)

  await page.getByRole('button', { name: 'Cấu hình' }).click()
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toBeVisible()
  await expectNoHorizontalOverflow(page)

  // Autofocus vào trường đầu tiên của form.
  await expect(page.getByTestId('config-role-prompt')).toBeFocused()

  // Đúng số control được expose: 4 ô chữ + 3 combobox, không switch/tab/nhãn Sắp có.
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('textbox')).toHaveCount(4)
  await expect(dialog.getByRole('combobox')).toHaveCount(3)
  await expect(dialog.getByRole('switch')).toHaveCount(0)
  await expect(dialog.getByRole('tab')).toHaveCount(0)
  await expect(dialog.getByText('Sắp có')).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: 'Lịch sử' })).toHaveCount(0)

  await page.keyboard.press('Escape')
  await expect(page.getByRole('heading', { name: 'Cấu hình trợ lý' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Cấu hình' })).toBeFocused()
  assertNoErrors()
})

test('bind lỗi không tăng count; bind và unbind thành công có confirmation lồng', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({ bindStatus: 410 })
  await login(page, state)
  await page.getByRole('button', { name: 'Thêm thiết bị' }).click()
  await page.getByLabel('Mã xác minh').fill('123456')
  await page.getByTestId('bind-submit').click()
  await expect(page.getByRole('alert')).toContainText('Mã kích hoạt không hợp lệ')

  state.bindStatus = 200
  await page.getByLabel('Mã xác minh').fill('654321')
  await page.getByTestId('bind-submit').click()
  await expect(page.getByRole('status')).toContainText('Đã liên kết thiết bị với Trợ lý gia đình')
  await page.getByRole('button', { name: 'Đóng', exact: true }).last().click()
  await expect(page.getByRole('button', { name: 'Thiết bị (1)' })).toBeVisible()

  await page.getByRole('button', { name: 'Thiết bị (1)' }).click()
  await page.getByRole('button', { name: 'Hủy liên kết' }).click()
  await expect(page.getByRole('heading', { name: 'Hủy liên kết thiết bị?' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('heading', { name: 'Hủy liên kết thiết bị?' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: /Thiết bị/ })).toBeVisible()
  await page.getByRole('button', { name: 'Hủy liên kết' }).click()
  await page.getByRole('button', { name: 'Hủy liên kết', exact: true }).last().click()
  await expect(page.getByText('Chưa có thiết bị')).toBeVisible()
  expect(state.devices).toEqual([])
  assertNoErrors()
})

test('OTA upload binary, tạo release và publish có xác nhận', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)
  await page.getByRole('button', { name: 'Vận hành' }).click()
  await page.getByRole('button', { name: 'Firmware OTA' }).click()
  await page.getByTestId('ota-file-input').setInputFiles({ name: 'firmware.bin', mimeType: 'application/octet-stream', buffer: Buffer.from([1, 2, 3, 4]) })
  await page.getByTestId('ota-upload-btn').click()
  await expect(page.getByTestId('ota-artifact-info').getByText('a'.repeat(64))).toBeVisible()
  await expectNoHorizontalOverflow(page)
  const upload = state.requests.find((request) => request.path === '/api/v1/control/ota/artifacts')
  expect(upload?.contentType).toBe('application/octet-stream')

  await page.getByTestId('ota-version').fill('2.4.3')
  await page.getByTestId('ota-board').fill('bread-compact-wifi-lcd')
  await page.getByTestId('ota-chip').fill('esp32s3')
  await page.getByTestId('ota-partition').fill('ota_0')
  await page.getByTestId('ota-create-release-btn').click()
  await expect(page.getByTestId('ota-release-info')).toContainText('Chưa xuất bản')
  await expectNoHorizontalOverflow(page)
  await page.getByTestId('ota-publish-btn').click()
  await expect(page.getByRole('heading', { name: 'Xác nhận xuất bản bản phát hành?' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByTestId('ota-publish-confirm').click()
  await expect(page.getByTestId('ota-publish-success')).toBeVisible()
  assertNoErrors()
})

test('lỗi /conversations chỉ làm suy giảm metadata, không mất danh sách trợ lý', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({ conversationStatus: 500 })
  await login(page, state)

  // Agents/devices vẫn hiển thị bình thường dù /conversations trả 500.
  await expect(page.getByRole('heading', { name: 'Trợ lý gia đình' })).toBeVisible()
  await expect(page.getByText('Chưa có dữ liệu')).toBeVisible()
  await expect(page.getByText('Không tải được dữ liệu')).toHaveCount(0)
  await expect(page.getByTestId('agents-retry')).toHaveCount(0)
  assertNoErrors()
})

test('401 cũ từ token phiên trước không đăng xuất phiên mới', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)

  // Giữ lại request hội thoại của phiên đầu để mô phỏng response đến muộn.
  state.holdPatterns.push({ method: 'GET', path: '/api/v1/control/conversations' })
  await page.getByRole('button', { name: 'Lịch sử' }).click()
  await expect(page.getByText('Đang tải lịch sử trò chuyện...')).toBeVisible()
  const stale = state.heldRequests.at(-1)
  expect(stale?.path).toContain('/api/v1/control/conversations')

  // Đóng hộp thoại rồi đăng xuất; token cũ bị thu hồi như trên server thật.
  await page.getByRole('dialog').getByRole('button', { name: 'Đóng', exact: true }).last().click()
  await page.getByRole('button', { name: 'Menu tài khoản' }).click()
  await page.getByRole('menuitem', { name: 'Đăng xuất' }).click()
  await expect(page.getByLabel('Email đăng nhập')).toBeVisible()

  // Đăng nhập lại tạo phiên mới với token mới hoạt động bình thường.
  await page.getByLabel('Email đăng nhập').fill(email)
  await page.getByLabel('Mật khẩu').fill(password)
  await page.getByRole('button', { name: 'Đăng nhập' }).click()
  await expect(page.getByRole('heading', { name: 'Trợ lý', exact: true })).toBeVisible()

  // Response 401 của token cũ đến muộn: không được xóa trắng phiên hiện tại.
  await stale!.respond(401, { detail: 'Invalid or expired session' })
  await expect(page.getByRole('heading', { name: 'Trợ lý', exact: true })).toBeVisible()
  await expect(page.getByLabel('Email đăng nhập')).toHaveCount(0)
  assertNoErrors()
})

test('đăng xuất khi server revoke lỗi 5xx vẫn về màn hình đăng nhập kèm cảnh báo', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({ logoutStatus: 500 })
  await login(page, state)

  await page.getByRole('button', { name: 'Menu tài khoản' }).click()
  await page.getByRole('menuitem', { name: 'Đăng xuất' }).click()
  await expect(page.getByLabel('Email đăng nhập')).toBeVisible()
  await expect(page.getByRole('status')).toContainText('chưa xác nhận thu hồi được phiên trên máy chủ')
  assertNoErrors()

  // Đăng nhập lại thành công phải xóa cảnh báo cũ.
  await page.getByLabel('Email đăng nhập').fill(email)
  await page.getByLabel('Mật khẩu').fill(password)
  await page.getByRole('button', { name: 'Đăng nhập' }).click()
  await expect(page.getByRole('heading', { name: 'Trợ lý', exact: true })).toBeVisible()
  await expect(page.getByRole('status')).toHaveCount(0)
})

test('đăng xuất khi mất kết nối tới server revoke hiển thị cảnh báo', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({ logoutNetworkError: true })
  await login(page, state)

  await page.getByRole('button', { name: 'Menu tài khoản' }).click()
  await page.getByRole('menuitem', { name: 'Đăng xuất' }).click()
  await expect(page.getByLabel('Email đăng nhập')).toBeVisible()
  await expect(page.getByRole('status')).toContainText('chưa xác nhận thu hồi được phiên trên máy chủ')
  assertNoErrors()
})

test('layout không tràn ngang và không có control enabled no-op', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)
  await expectNoHorizontalOverflow(page)
  await expect(page.getByRole('button', { name: 'Nhân bản giọng nói' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Kho kiến thức' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Chọn ngôn ngữ/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Chọn giao diện/ })).toHaveCount(0)
  assertNoErrors()
})

test('các hộp thoại chính không gây tràn ngang ở mọi viewport', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)

  // Tạo trợ lý.
  await page.getByRole('button', { name: 'Tạo trợ lý' }).click()
  await page.getByRole('menuitem', { name: 'Tạo trợ lý mới' }).click()
  await expect(page.getByRole('heading', { name: 'Tạo trợ lý mới' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByRole('button', { name: 'Hủy', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Tạo trợ lý mới' })).toHaveCount(0)

  // Đổi tên.
  await page.getByRole('button', { name: 'Thao tác' }).click()
  await page.getByRole('menuitem', { name: 'Đổi tên' }).click()
  await expect(page.getByRole('heading', { name: 'Đổi tên trợ lý' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByRole('button', { name: 'Hủy', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Đổi tên trợ lý' })).toHaveCount(0)

  // Xác nhận xóa.
  await page.getByRole('button', { name: 'Thao tác' }).click()
  await page.getByRole('menuitem', { name: 'Xóa trợ lý' }).click()
  await expect(page.getByRole('heading', { name: 'Xóa trợ lý?' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByTestId('delete-cancel').click()
  await expect(page.getByRole('heading', { name: 'Xóa trợ lý?' })).toHaveCount(0)

  // Lịch sử hội thoại.
  await page.getByRole('button', { name: 'Lịch sử' }).click()
  await expect(page.getByRole('heading', { name: /Lịch sử trò chuyện/ })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByRole('dialog').getByRole('button', { name: 'Đóng', exact: true }).last().click()
  await expect(page.getByRole('heading', { name: /Lịch sử trò chuyện/ })).toHaveCount(0)

  // Danh sách thiết bị của trợ lý.
  await page.getByRole('button', { name: /Thiết bị \(0\)/ }).click()
  await expect(page.getByRole('heading', { name: /· Thiết bị/ })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByRole('button', { name: 'Đóng', exact: true }).last().click()
  await expect(page.getByRole('heading', { name: /· Thiết bị/ })).toHaveCount(0)

  // Thêm thiết bị từ toolbar.
  await page.getByRole('button', { name: 'Thêm thiết bị' }).click()
  await expect(page.getByRole('heading', { name: 'Thêm thiết bị' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByRole('button', { name: 'Hủy', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Thêm thiết bị' })).toHaveCount(0)

  assertNoErrors()
})

test('điều hướng M6 hiển thị mọi khu vực vận hành và quản trị responsive', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)

  await page.getByRole('button', { name: 'Vận hành' }).click()
  for (const [tab, heading] of [
    ['Nhà cung cấp', 'Nhà cung cấp'],
    ['Kho kiến thức', 'Kho kiến thức'],
    ['Hiệu chỉnh & ngữ cảnh', 'Hiệu chỉnh & ngữ cảnh'],
    ['Tích hợp & thiết bị', 'Tích hợp & thiết bị'],
    ['Firmware OTA', 'Firmware OTA'],
  ] as const) {
    await page.getByRole('button', { name: tab, exact: true }).click()
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    await expectNoHorizontalOverflow(page)
  }

  await page.getByRole('button', { name: 'Quản trị' }).click()
  for (const [tab, heading] of [
    ['User', 'Quản lý người dùng'],
    ['Cài đặt & quota', 'Cài đặt & Quota'],
    ['Audit', 'Nhật ký audit'],
  ] as const) {
    await page.getByRole('button', { name: tab, exact: true }).click()
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    await expectNoHorizontalOverflow(page)
  }
  assertNoErrors()
})

test('knowledge upload text và truy vấn RAG dùng đúng HTTP contract', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)
  await page.getByRole('button', { name: 'Vận hành' }).click()
  await page.getByRole('button', { name: 'Kho kiến thức' }).click()

  await page.getByTestId('document-file-input').setInputFiles({
    name: 'van-hanh.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# Veetee\nChạy trực tiếp trên máy local.'),
  })
  await page.getByTestId('upload-document-btn').click()
  await expect(page.getByText('van-hanh.md')).toBeVisible()
  const upload = state.requests.find(request => request.method === 'PUT' && request.path.includes('/knowledge/datasets/'))
  expect(upload?.contentType).toBe('text/markdown')

  await page.getByPlaceholder('Nhập câu hỏi / từ khóa tìm kiếm...').fill('Veetee chạy ở đâu?')
  await page.getByRole('button', { name: 'Tìm kiếm' }).click()
  await expect(page.getByText('Veetee chạy trực tiếp trên máy local.')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  assertNoErrors()
})

test('correction preview và device MCP bắt buộc prepare rồi xác nhận rõ ràng', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({
    devices: [{ id: '44444444-4444-4444-8444-444444444444', device_id: 'veetee-device', alias: 'Thiết bị phòng khách', agent_id: stateAgentId(), online: true, last_seen_at: '2026-08-22T10:00:00Z' }],
  })
  await login(page, state)
  await page.getByRole('button', { name: 'Vận hành' }).click()
  await page.getByRole('button', { name: 'Hiệu chỉnh & ngữ cảnh' }).click()
  await page.getByRole('button', { name: 'Chạy thử' }).click()
  await expect(page.getByText('Xin chào, chau ten la Veetee.')).toBeVisible()

  await page.getByRole('button', { name: 'Tích hợp & thiết bị' }).click()
  await page.getByRole('button', { name: 'Device MCP Tools' }).click()
  await page.getByRole('button', { name: 'Tải danh sách MCP Tools' }).click()
  await expect(page.getByLabel('Chọn công cụ MCP:')).toHaveValue('screen.set_brightness')
  await page.getByTestId('device-mcp-call-btn').click()
  await expect(page.getByTestId('mcp-confirm-modal')).toBeVisible()
  await expect(page.getByText('secret-confirmation-token-never-render')).toHaveCount(0)
  expect(state.requests.some(request => request.path.endsWith('/prepare-call'))).toBe(true)
  expect(state.requests.find(request => request.path.endsWith('/prepare-call'))?.body).toMatchObject({ session_id: 'live-session-1', arguments: {} })
  expect(state.requests.some(request => request.path.endsWith('/call'))).toBe(false)
  await page.getByTestId('mcp-confirm-submit-btn').click()
  await expect(page.getByText('Đã cập nhật độ sáng')).toBeVisible()
  expect(state.requests.some(request => request.path.endsWith('/call'))).toBe(true)
  assertNoErrors()
})

test('403 admin hiển thị role gate nhưng giữ nguyên phiên đăng nhập', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState({ forbiddenPaths: ['/api/v1/control/admin/users'] })
  await login(page, state)
  await page.getByRole('button', { name: 'Quản trị' }).click()
  await expect(page.getByTestId('role-gate')).toBeVisible()
  await expect(page.getByLabel('Email đăng nhập')).toHaveCount(0)
  await page.getByRole('button', { name: 'Trợ lý', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Trợ lý', exact: true })).toBeVisible()
  assertNoErrors()
})

function stateAgentId() {
  return '11111111-1111-4111-8111-111111111111'
}
