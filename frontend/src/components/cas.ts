// Проверка CAS-номера по контрольной цифре — то же детерминированное правило,
// что и в `backend/app/services/cas.py`. На клиенте оно нужно затем, чтобы
// форма сама различала номер и название и не спрашивала об этом закупщика.

const CAS_RE = /^(\d{2,7})-(\d{2})-(\d)$/;

// Разделители, которыми в живых данных оказывается дефис: копипаста из Word и
// PDF даёт неразрывный дефис, китайские страницы — полноширинный.
const DASHES: Record<string, string> = {
  "‐": "-",
  "‑": "-",
  "‒": "-",
  "–": "-",
  "—": "-",
  "―": "-",
  "−": "-",
  "－": "-",
  " ": "",
};

export function normalizeCas(value: string): string {
  return (value || "")
    .split("")
    .map((char) => (char in DASHES ? DASHES[char] : char))
    .join("")
    .trim();
}

function checkDigit(body: string): number {
  return (
    [...body]
      .reverse()
      .reduce((sum, digit, index) => sum + Number(digit) * (index + 1), 0) % 10
  );
}

export function isValidCas(value: string): boolean {
  const match = CAS_RE.exec(normalizeCas(value));
  if (!match) return false;
  return checkDigit(match[1] + match[2]) === Number(match[3]);
}

// Опечатка в контрольной цифре — единственная, которую можно исправить не
// гадая. null означает, что подсказать нечего.
export function suggestCheckDigit(value: string): string | null {
  const match = CAS_RE.exec(normalizeCas(value));
  if (!match) return null;
  const correct = checkDigit(match[1] + match[2]);
  if (correct === Number(match[3])) return null;
  return `${match[1]}-${match[2]}-${correct}`;
}
