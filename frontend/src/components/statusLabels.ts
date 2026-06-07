// Единый язык статусов (раздел 16 UI/UX-плана): русские подписи и цвета бейджей.

import type { RFQStatus } from "../api/types";

export const STATUS_LABELS: Record<RFQStatus, string> = {
  draft: "Черновик",
  verified: "Вещество проверено",
  sent: "RFQ разослан",
  collecting: "Сбор ответов",
  parsed: "Ответы извлечены",
  summarized: "Сводка готова",
  escalated: "Ручной разбор",
  closed: "Закрыт",
};

// Группы цветов: нейтральные / синие (в работе) / зелёные / янтарные.
export const STATUS_TONE: Record<RFQStatus, "neutral" | "info" | "ok" | "warn"> = {
  draft: "neutral",
  verified: "info",
  sent: "neutral",
  collecting: "info",
  parsed: "info",
  summarized: "ok",
  escalated: "warn",
  closed: "ok",
};
