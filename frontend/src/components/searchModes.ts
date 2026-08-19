// Режим поиска: сколько компаний агент откроет и проверит в каждой стране.
//
// Раньше на этом месте стояло число с подписью «сколько поставщиков найти».
// Подпись обещала результат, которого поиск не обещает: замер по 211
// прогонам с пятёркой показал пять проверенных компаний, из которых
// производителем оказывалась в среднем одна. Число же на деле управляет не
// находками, а объёмом работы — сколько страниц открыть и прочитать.
// Режимы называют это прямо.
//
// Число поисковых запросов режим не меняет: план всегда из восьми, плюс до
// трёх добавочных на поиск сайтов по именам компаний.

export type SearchModeKey = "fast" | "normal" | "thorough";

export interface SearchMode {
  key: SearchModeKey;
  label: string;
  companies: number;
  hint: string;
}

export const SEARCH_MODES: SearchMode[] = [
  {
    key: "fast",
    label: "Быстрый",
    companies: 5,
    hint: "5 компаний в каждой стране. Хватает, чтобы понять, есть ли предложение вообще.",
  },
  {
    key: "normal",
    label: "Обычный",
    companies: 10,
    hint: "10 компаний в каждой стране. Обычный объём для рабочего запроса.",
  },
  {
    key: "thorough",
    label: "Тщательный",
    companies: 20,
    hint: "20 компаний в каждой стране. Дольше и дороже, зато шире охват — для редких веществ.",
  },
];

export const DEFAULT_SEARCH_MODE: SearchModeKey = "normal";

export function modeCompanies(key: SearchModeKey): number {
  return (
    SEARCH_MODES.find((mode) => mode.key === key)?.companies ??
    SEARCH_MODES[1].companies
  );
}

// Запрос мог быть создан до появления режимов или другим числом — берём
// ближайший режим, иначе выбор оказался бы пустым.
export function modeFromCompanies(companies: number | null | undefined): SearchModeKey {
  if (typeof companies !== "number") return DEFAULT_SEARCH_MODE;
  return SEARCH_MODES.reduce((closest, mode) =>
    Math.abs(mode.companies - companies) < Math.abs(closest.companies - companies)
      ? mode
      : closest,
  ).key;
}
