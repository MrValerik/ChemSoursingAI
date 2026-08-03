// Тема хранится на <html data-theme>, а не в React-состоянии: её должен
// увидеть CSS до первой отрисовки. Первичная установка живёт в index.html,
// здесь — только переключение и подписка.

export type Theme = "light" | "dark";

const STORAGE_KEY = "chemsource-theme";

export function readTheme(): Theme {
  const attr = document.documentElement.dataset.theme;
  if (attr === "light" || attr === "dark") return attr;
  return "light";
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Приватный режим: тема продержится до конца сессии, и это не повод падать.
  }
}
