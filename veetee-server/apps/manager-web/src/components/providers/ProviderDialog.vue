<script setup lang="ts">
import { VtButton, VtField, VtInput, VtSelect } from "../ui";
</script>

<template>
  <div id="providerModal" class="modal-backdrop" hidden>
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="providerTitle">
      <button class="modal-close" type="button" data-close-provider-modal aria-label="Đóng">×</button>
      <span class="modal-kicker">PROVIDER BINDING</span>
      <h2 id="providerTitle">Cấu hình provider</h2>
      <form class="provider-edit-form">
        <input type="hidden" name="providerId" />
        <VtField label="Adapter" required>
          <VtInput name="adapter" maxlength="120" required />
        </VtField>
        <VtField label="Model" required>
          <VtInput name="model" maxlength="200" required />
        </VtField>
        <VtField label="Base URL" hint="Để trống khi adapter không cần HTTP endpoint.">
          <VtInput name="baseUrl" placeholder="http://127.0.0.1:20128/v1" />
        </VtField>
        <div class="two-cols">
          <VtField label="Priority" required>
            <VtInput name="priority" type="number" min="0" max="1000" required />
          </VtField>
          <VtField label="Locales" required>
            <VtInput name="locales" placeholder="vi-VN, en-US" required />
          </VtField>
        </div>
        <div class="two-cols">
          <VtField label="Trạng thái">
            <VtSelect name="enabled">
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </VtSelect>
          </VtField>
          <VtField label="Secret">
            <VtSelect name="secretAction">
              <option value="keep">Giữ nguyên</option>
              <option value="rotate">Thay secret</option>
              <option value="clear">Xóa secret</option>
            </VtSelect>
          </VtField>
        </div>
        <VtField label="Secret mới" hint="Manager không bao giờ đọc ngược secret hiện tại.">
          <VtInput name="secret" type="password" autocomplete="new-password" placeholder="Chỉ nhập khi chọn Thay secret" />
        </VtField>
        <VtButton class="modal-submit" type="submit">Lưu provider</VtButton>
      </form>
    </div>
  </div>
</template>
