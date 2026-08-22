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

test('không quảng bá cấu hình runtime chưa có hiệu lực', async ({ page }) => {
  const assertNoErrors = failOnBrowserErrors(page)
  const state = createState()
  await login(page, state)
  await expect(page.getByRole('button', { name: 'Cấu hình' })).toHaveCount(0)
  await expect(page.getByText('groq/openai/gpt-oss-120b')).toBeVisible()
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
