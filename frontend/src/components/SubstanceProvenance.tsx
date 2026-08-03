// Происхождение данных о веществе и исход проверки номера.
//
// Зачем это отдельным компонентом. Раньше неудачная проверка выглядела
// одинаково во всех случаях: «вещество не верифицировано». Но опечатка в
// номере, отсутствие вещества в PubChem и недоступность самого PubChem —
// три разных факта, и только первый говорит, что пользователь ошибся.
// Третий вообще не про вещество: это сбой на нашей стороне, и показывать
// его как свойство вещества значит вводить закупщика в заблуждение.
//
// Та же логика у провенанса: находка ИИ-агента без пометки через месяц
// неотличима от справочных данных.

import type { FieldSource, VerificationOutcome } from "../api/types";

const SOURCE_LABELS: Record<FieldSource, string> = {
  pubchem: "Проверено по PubChem",
  ai_agent: "Поиск от ИИ-агента",
  human: "Указано специалистом",
  catalog: "Из справочника компании",
};

const SOURCE_HINTS: Record<FieldSource, string> = {
  pubchem: "Значение получено из открытой базы PubChem по CAS-номеру.",
  ai_agent:
    "Значение нашёл ИИ-агент. Независимой проверкой оно не подтверждено — сверьте перед закупочным решением.",
  human: "Значение ввёл сотрудник, автоматической проверки не было.",
  catalog: "Значение взято из карточки вещества, подтверждённой ранее.",
};

// Данные агента не считаются подтверждением: их проверяет человек, а не
// независимый источник.
const VERIFYING_SOURCES: FieldSource[] = ["pubchem", "catalog"];

export function FieldSourceBadge({ source }: { source: FieldSource | null }) {
  if (!source) return null;
  const tone = VERIFYING_SOURCES.includes(source) ? "tone-ok" : "tone-neutral";
  return (
    <span className={`badge ${tone}`} title={SOURCE_HINTS[source]}>
      {SOURCE_LABELS[source]}
    </span>
  );
}

interface OutcomeCopy {
  tone: string;
  title: string;
  text: string;
}

const OUTCOMES: Record<VerificationOutcome, OutcomeCopy> = {
  confirmed: {
    tone: "tone-ok",
    title: "Вещество подтверждено",
    text: "PubChem знает этот номер. Перед закупочным решением всё равно сверьте вещество со спецификацией и CoA.",
  },
  not_found: {
    tone: "tone-neutral",
    title: "PubChem не знает это вещество",
    text: "Это не значит, что вещества не существует: смесей, рецептур, полимеров и промышленных продуктов там нет и не будет. Запрос можно вести дальше — под ответственность специалиста.",
  },
  invalid_checksum: {
    tone: "tone-warn",
    title: "В номере ошибка",
    text: "Контрольная цифра не сходится — так выглядит опечатка. Проверьте номер: искать по нему бессмысленно.",
  },
  unavailable: {
    tone: "tone-warn",
    title: "Проверка не выполнена",
    text: "PubChem не ответил. О самом веществе это не говорит ничего — повторите проверку позже.",
  },
};

export function VerificationNotice({
  outcome,
}: {
  outcome: VerificationOutcome | null;
}) {
  if (!outcome) return null;
  const copy = OUTCOMES[outcome];
  if (!copy) return null;
  return (
    <p className="note">
      <span className={`badge ${copy.tone}`}>{copy.title}</span> {copy.text}
    </p>
  );
}
