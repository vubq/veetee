<script setup lang="ts">
import { useId } from "vue";
import { useI18n } from "vue-i18n";

import { useTheme, type ThemePreference } from "../../theme";

const { t } = useI18n();
const groupName = `manager-theme-${useId().replace(/:/g, "")}`;
const { preference, setPreference } = useTheme();
const options: Array<{ value: ThemePreference; labelKey: string; descriptionKey: string }> = [
  { value: "light", labelKey: "theme.light", descriptionKey: "theme.lightDescription" },
  { value: "system", labelKey: "theme.system", descriptionKey: "theme.systemDescription" },
  { value: "dark", labelKey: "theme.dark", descriptionKey: "theme.darkDescription" },
];
</script>

<template>
  <fieldset class="vt-theme-selector" data-theme-selector>
    <legend>{{ t("theme.label") }}</legend>
    <div class="vt-theme-options">
      <label
        v-for="option in options"
        :key="option.value"
        :class="{ active: preference === option.value }"
        :data-theme-option="option.value"
      >
        <input
          type="radio"
          :name="groupName"
          :value="option.value"
          :checked="preference === option.value"
          @change="setPreference(option.value)"
        />
        <span aria-hidden="true"><i></i></span>
        <b>{{ t(option.labelKey) }}</b>
        <small>{{ t(option.descriptionKey) }}</small>
      </label>
    </div>
  </fieldset>
</template>
