// Подписи к ходу работ по заявке: стадия конвейера и ближайшее действие.
// Коды приходят с сервера (backend/app/services/rfq_progress.py) — здесь
// только русские формулировки и правила их склейки с числами, чтобы
// текст правился без выката бэкенда.

import type { RFQListItem, RFQNextAction, RFQStage } from "../api/types";

// Четыре сегмента полоски прогресса в строке. Статус RFQ подробнее, но
// в списке важно не «что система сделала», а «далеко ли до конца».
export const STAGE_ORDER: RFQStage[] = [
  "search",
  "dispatch",
  "dialogue",
  "summary",
];

export const STAGE_LABELS: Record<RFQStage, string> = {
  search: "Поиск",
  dispatch: "Рассылка",
  dialogue: "Переписка",
  summary: "Сводка",
};

// Причины эскалации — те же подписи, что в разделе «Ручной разбор».
const REASON_LABELS: Record<string, string> = {
  grade: "вопрос по грейду",
  logistics: "опасная логистика",
  shortage: "дефицит",
  custom_synthesis: "кастомный синтез",
  low_confidence: "низкая уверенность извлечения",
  other: "без причины",
};

// Тон чипа: ждать / делать / горит. «wait» намеренно тусклый — строка,
// по которой сейчас ничего не нужно, не должна тянуть взгляд.
export type ActionTone = "wait" | "todo" | "alert" | "done";

export const ACTION_TONE: Record<RFQNextAction, ActionTone> = {
  escalation: "alert",
  dispatch_error: "alert",
  reply: "alert",
  silence: "todo",
  decide: "done",
  dispatch: "todo",
  verify: "todo",
  search: "todo",
  incomplete: "wait",
  waiting: "wait",
  closed: "wait",
};

// Порядок сортировки по срочности — копия ACTION_URGENCY на сервере.
const ACTION_URGENCY: Record<RFQNextAction, number> = {
  escalation: 0,
  dispatch_error: 1,
  reply: 2,
  silence: 3,
  decide: 4,
  dispatch: 5,
  verify: 6,
  search: 7,
  incomplete: 8,
  waiting: 9,
  closed: 10,
};

export const actionUrgency = (row: RFQListItem) =>
  ACTION_URGENCY[row.next_action] ?? 99;

// Русские числительные: «1 день», «2 дня», «5 дней».
const plural = (n: number, one: string, few: string, many: string) => {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return many;
  const mod10 = n % 10;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
};

export const days = (n: number) => `${n} ${plural(n, "день", "дня", "дней")}`;

const letters = (n: number) =>
  `${n} ${plural(n, "письмо", "письма", "писем")}`;

const companies = (n: number) =>
  `${n} ${plural(n, "компания", "компании", "компаний")}`;

export interface ActionChip {
  label: string;
  // Уточнение под чипом: цифры, из которых видно, почему он такой.
  detail: string | null;
  tone: ActionTone;
}

// Ближайшее действие одной строкой. Подпись обязана отвечать на «что мне
// сделать», а уточнение — на «почему я должен в это поверить»: без цифр
// чип превращается в такой же безадресный ярлык, каким был статус.
export function actionChip(row: RFQListItem): ActionChip {
  const tone = ACTION_TONE[row.next_action] ?? "wait";
  const waited = row.waiting_days;

  switch (row.next_action) {
    case "escalation": {
      const reasons = row.escalation_reasons
        .map((reason) => REASON_LABELS[reason] ?? reason)
        .join(", ");
      return {
        label: "Разобрать вручную",
        detail: reasons || null,
        tone,
      };
    }
    case "dispatch_error":
      return {
        label: "Не ушло письмо",
        detail: `${letters(row.n_dispatch_errors)} с ошибкой канала`,
        tone,
      };
    case "reply":
      return {
        label: "Ответить поставщику",
        detail: `${companies(row.n_awaiting_our_reply)} ждут нашего ответа`,
        tone,
      };
    case "silence":
      return {
        label: "Напомнить о себе",
        detail:
          waited === null
            ? `молчат ${row.n_silent} из ${row.n_recipients}`
            : `молчат ${row.n_silent} из ${row.n_recipients} · ${days(waited)}`,
        tone,
      };
    case "decide":
      return {
        label: "Сравнить предложения",
        detail: `${row.n_quotations} собрано полностью`,
        tone,
      };
    case "incomplete":
      // completeness_pct — доля полных котировок, а не заполненных полей;
      // «0%» без знаменателя читалось бы как «ничего не пришло».
      return {
        label: "Собираем данные",
        detail: `заполнены ${row.n_complete} из ${row.n_quotations}`,
        tone,
      };
    case "waiting":
      return {
        label: "Ждём ответов",
        detail:
          waited === null
            ? `разослано ${row.n_recipients}`
            : `${days(waited)} без ответов`,
        tone,
      };
    case "dispatch":
      return {
        label: "Разослать запросы",
        detail: `найдено ${row.n_suppliers_found}`,
        tone,
      };
    case "verify":
      return { label: "Проверить вещество", detail: null, tone };
    case "search":
      return { label: "Найти поставщиков", detail: null, tone };
    case "closed":
      return { label: "Закрыт", detail: null, tone };
    default:
      return { label: "—", detail: null, tone };
  }
}
