<script setup>
defineProps({
  message: { type: String, default: '' },
  tone: { type: String, default: 'info' },
})
</script>

<template>
  <Teleport to="body">
    <transition name="toast-rise">
      <div v-if="message" class="ui-toast" :class="`ui-toast--${tone}`" role="status">
        <slot>{{ message }}</slot>
      </div>
    </transition>
  </Teleport>
</template>
