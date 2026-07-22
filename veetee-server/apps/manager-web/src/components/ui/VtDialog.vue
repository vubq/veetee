<script setup lang="ts">
import { Dialog, DialogPanel, DialogTitle, TransitionChild, TransitionRoot } from "@headlessui/vue";

import type { VtIconName } from "./VtIcon.vue";
import VtIcon from "./VtIcon.vue";

withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    description?: string;
    width?: "sm" | "md" | "lg";
    eyebrow?: string;
    icon?: VtIconName;
  }>(),
  { description: "", width: "md", eyebrow: "VEETEE MANAGER" },
);
const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <TransitionRoot appear :show="open" as="template">
    <Dialog class="vt-dialog-layer" @close="emit('close')">
      <TransitionChild as="template" enter="dialog-backdrop-enter" enter-from="dialog-backdrop-from" enter-to="dialog-backdrop-to" leave="dialog-backdrop-leave" leave-from="dialog-backdrop-to" leave-to="dialog-backdrop-from">
        <div class="vt-dialog-backdrop"></div>
      </TransitionChild>
      <div class="vt-dialog-scroll">
        <TransitionChild as="template" enter="dialog-panel-enter" enter-from="dialog-panel-from" enter-to="dialog-panel-to" leave="dialog-panel-leave" leave-from="dialog-panel-to" leave-to="dialog-panel-from">
          <DialogPanel class="vt-dialog" :class="`is-${width}`">
          <header class="vt-dialog-header">
            <div class="vt-dialog-heading" :class="{ 'has-icon': icon }">
              <span v-if="icon" class="vt-dialog-icon"><VtIcon :name="icon" :size="21" /></span>
              <div><span class="vt-kicker">{{ eyebrow }}</span><DialogTitle as="h2">{{ title }}</DialogTitle><p v-if="description">{{ description }}</p></div>
            </div>
            <button class="vt-icon-button" type="button" aria-label="Đóng" @click="emit('close')"><VtIcon name="close" :size="19" /></button>
          </header>
          <div class="vt-dialog-body"><slot /></div>
          <footer v-if="$slots.footer" class="vt-dialog-footer"><slot name="footer" /></footer>
          </DialogPanel>
        </TransitionChild>
      </div>
    </Dialog>
  </TransitionRoot>
</template>
