<script setup lang="ts">
const year = new Date().getFullYear()
</script>

<template>
  <footer class="app-footer">
    <div class="page-container footer-inner">
      <div class="footer-meta">
        <span>© {{ year }} Veetee AI Control Panel</span>
        <span class="footer-separator" aria-hidden="true"></span>
        <span>Phiên bản giao diện 0.1</span>
      </div>
      <p>Dịch vụ sử dụng công nghệ trí tuệ nhân tạo. Nội dung do AI tạo ra chỉ mang tính tham khảo.</p>
    </div>
  </footer>
</template>
