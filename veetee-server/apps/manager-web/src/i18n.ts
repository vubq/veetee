import { createI18n } from "vue-i18n";

import { enUS } from "./locales/en-US";
import { viVN } from "./locales/vi-VN";

export const i18n = createI18n({
  legacy: false,
  locale: "vi-VN",
  fallbackLocale: "en-US",
  messages: { "vi-VN": viVN, "en-US": enUS },
});
