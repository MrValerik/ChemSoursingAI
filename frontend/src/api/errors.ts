type ValidationIssue = {
  type?: string;
  loc?: Array<string | number>;
  msg?: string;
  ctx?: Record<string, unknown>;
};

const DEFAULT_ERROR =
  "Не удалось выполнить операцию. Повторите попытку. Если ошибка сохранится, обратитесь к администратору.";

const FIELD_LABELS: Record<string, string> = {
  username: "Логин",
  password: "Пароль",
  email: "Email",
  cas: "CAS-номер",
  name: "Наименование вещества",
  preferred_name: "Предпочтительное наименование",
  synonyms: "Допустимые синонимы",
  excluded_names: "Исключённые названия",
  notes: "Комментарий специалиста",
  purity: "Чистота",
  application: "Применение",
  volume: "Объём",
  target_price: "Целевая цена",
  currency: "Валюта",
  incoterms: "Условия поставки",
  search_countries: "Страны поиска",
  supplier_target: "Количество поставщиков",
  additional_instructions: "Дополнительные требования",
  subject: "Тема письма",
  body: "Текст письма",
};

const EXACT_MESSAGES: Record<string, string> = {
  "RFQ не найден":
    "Запрос не найден. Возможно, он был удалён или недоступен для вашей учётной записи.",
  "RFQ not found":
    "Запрос не найден. Возможно, он был удалён или недоступен для вашей учётной записи.",
  "Search run not found":
    "Запуск поиска не найден. Обновите страницу и выберите актуальный запуск.",
  "Substance not found":
    "Химическое вещество не найдено. Возможно, карточка была удалена.",
  "Recipient not found":
    "Получатель не найден. Обновите список поставщиков и повторите попытку.",
  "Escalation not found":
    "Задача ручной проверки не найдена. Возможно, её уже обработал другой пользователь.",
  "Template not found":
    "Шаблон не найден. Возможно, он был удалён.",
  "Prompt not found":
    "Настройка ИИ-агента не найдена. Обновите страницу и повторите попытку.",
  "Not Found": "Запрошенные данные не найдены.",
  "Internal Server Error":
    "На сервере произошла ошибка. Повторите попытку позже или обратитесь к администратору.",
  "Bad Gateway":
    "Сервер временно не может получить ответ от связанного сервиса. Повторите попытку позже.",
  "Service Unavailable":
    "Сервис временно недоступен. Повторите попытку позже.",
  "Gateway Timeout":
    "Связанный сервис не ответил вовремя. Повторите попытку позже.",
  invalid_cas_checksum:
    "CAS-номер не прошёл проверку контрольной суммы. Проверьте правильность цифр.",
  not_found:
    "Данные не найдены во внешнем справочнике. Проверьте CAS-номер или название вещества.",
};

function hasCyrillic(value: string): boolean {
  return /[А-Яа-яЁё]/.test(value);
}

function fieldLabel(issue: ValidationIssue): string {
  const field = [...(issue.loc ?? [])]
    .reverse()
    .find((part): part is string => typeof part === "string" && part !== "body");
  return field ? (FIELD_LABELS[field] ?? field.split("_").join(" ")) : "Данные";
}

function validationReason(issue: ValidationIssue): string {
  const type = issue.type ?? "";
  const ctx = issue.ctx ?? {};

  if (type === "missing") return "обязательное поле не заполнено";
  if (type.includes("string_too_short")) {
    return `укажите не менее ${String(ctx.min_length ?? 1)} символов`;
  }
  if (type.includes("string_too_long")) {
    return `укажите не более ${String(ctx.max_length ?? "")} символов`;
  }
  if (type.includes("greater_than_equal")) {
    return `значение должно быть не меньше ${String(ctx.ge ?? ctx.limit_value ?? "")}`;
  }
  if (type.includes("less_than_equal")) {
    return `значение должно быть не больше ${String(ctx.le ?? ctx.limit_value ?? "")}`;
  }
  if (type.includes("greater_than")) {
    return `значение должно быть больше ${String(ctx.gt ?? ctx.limit_value ?? "")}`;
  }
  if (type.includes("less_than")) {
    return `значение должно быть меньше ${String(ctx.lt ?? ctx.limit_value ?? "")}`;
  }
  if (type.includes("int_parsing")) return "введите целое число";
  if (type.includes("float_parsing") || type.includes("decimal_parsing")) {
    return "введите число";
  }
  if (type.includes("bool_parsing")) return "выберите допустимое значение";
  if (type.includes("enum") || type.includes("literal")) {
    return "выберите одно из допустимых значений";
  }
  if (type.includes("list_too_short")) {
    return `выберите не менее ${String(ctx.min_length ?? 1)} вариантов`;
  }
  if (type.includes("list_too_long")) {
    return `выберите не более ${String(ctx.max_length ?? "")} вариантов`;
  }

  const contextError = String(ctx.error ?? "").trim();
  if (hasCyrillic(contextError)) return contextError;
  if (issue.msg && hasCyrillic(issue.msg)) return issue.msg;
  return "значение заполнено неверно";
}

function validationMessage(issues: ValidationIssue[]): string {
  if (issues.length === 0) {
    return "Проверьте заполненные поля и исправьте отмеченные значения.";
  }
  const descriptions = issues
    .slice(0, 3)
    .map((issue) => `«${fieldLabel(issue)}»: ${validationReason(issue)}`);
  const suffix =
    issues.length > 3 ? " Исправьте также остальные отмеченные поля." : "";
  return `Проверьте данные: ${descriptions.join("; ")}.${suffix}`;
}

function technicalMessage(value: string): string | null {
  const normalized = value.trim();
  const lower = normalized.toLowerCase();
  const exact = EXACT_MESSAGES[normalized];
  if (exact) return exact;
  if (/supplier\s+\d+\s+not found/i.test(normalized)) {
    return "Поставщик не найден. Возможно, карточка была удалена.";
  }
  if (lower.includes("pubchem")) {
    if (lower.includes("invalid_cas_checksum")) {
      return "CAS-номер не прошёл проверку контрольной суммы. Проверьте правильность цифр.";
    }
    if (lower.includes("not_found")) {
      return "Справочник PubChem не нашёл вещество. Проверьте CAS-номер или название.";
    }
    return "Не удалось получить данные из справочника PubChem. Повторите проверку позже.";
  }
  if (
    lower.includes("qwen") ||
    lower.includes("ollama") ||
    lower.includes("llm")
  ) {
    return "ИИ-агент не смог получить корректный ответ локальной модели. Запустите этап повторно; если ошибка сохранится, обратитесь к администратору.";
  }
  if (
    lower.includes("timeout") ||
    lower.includes("timed out") ||
    lower.includes("readtimeout")
  ) {
    return "Сервис не ответил за отведённое время. Повторите попытку позже.";
  }
  if (
    lower.includes("failed to fetch") ||
    lower.includes("networkerror") ||
    lower.includes("network error") ||
    lower.includes("connection refused") ||
    lower.includes("connecterror") ||
    lower.includes("all connection attempts failed") ||
    lower.includes("http_error")
  ) {
    return "Не удалось установить соединение с сервисом. Проверьте подключение и повторите попытку.";
  }
  if (
    lower.includes("model") &&
    (lower.includes("недоступ") ||
      lower.includes("unavailable") ||
      lower.includes("connection"))
  ) {
    return "Локальная ИИ-модель недоступна. Убедитесь, что сервис модели запущен, и повторите попытку.";
  }
  return null;
}

export function userErrorMessage(
  value: unknown,
  fallback = DEFAULT_ERROR,
): string {
  if (value instanceof Error) {
    return userErrorMessage(value.message, fallback);
  }
  if (typeof value !== "string") return fallback;

  const message = value.trim().replace(/^Error:\s*/i, "");
  if (!message) return fallback;
  const translated = technicalMessage(message);
  if (translated) return translated;
  if (hasCyrillic(message)) return message;
  return fallback;
}

export function apiResponseErrorMessage(
  status: number,
  detail: unknown,
  path: string,
): string {
  if (Array.isArray(detail)) {
    return validationMessage(detail as ValidationIssue[]);
  }

  if (typeof detail === "string" && detail.trim()) {
    const translated = technicalMessage(detail);
    if (translated) return translated;
    if (hasCyrillic(detail)) return detail.trim();
  }

  if (status === 400) {
    return "Сервер не смог обработать запрос. Проверьте введённые данные.";
  }
  if (status === 401) {
    return path.startsWith("/auth/login")
      ? "Неверный логин или пароль. Проверьте данные и попробуйте снова."
      : "Срок действия сеанса истёк. Войдите в систему повторно.";
  }
  if (status === 403) {
    return "У вас недостаточно прав для этого действия. Обратитесь к администратору.";
  }
  if (status === 404) {
    return "Запрошенные данные не найдены. Возможно, они были удалены.";
  }
  if (status === 409) {
    return "Действие невозможно в текущем состоянии данных. Обновите страницу и повторите попытку.";
  }
  if (status === 422) {
    return "Проверьте заполненные поля и исправьте отмеченные значения.";
  }
  if (status === 429) {
    return "Слишком много запросов за короткое время. Подождите немного и повторите попытку.";
  }
  if (status >= 500) {
    return "Сервис временно недоступен. Повторите попытку позже или обратитесь к администратору.";
  }
  return DEFAULT_ERROR;
}
