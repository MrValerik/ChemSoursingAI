// Отметки названий вещества: что считать тем же веществом, а что нет.
//
// Зачем. Когда CAS-номера нет, якорем поиска становится название — а оно
// неуникально. У бетаина и его гидрохлорида названия соседние, вещества
// разные, и поиск по неверному синониму найдёт настоящих поставщиков
// настоящего вещества. Провал будет выглядеть как успех.
//
// Отличить их может только человек: закупщик точно знает, нужен ему
// гидрохлорид или нет. Поэтому названия отмечаются, а не принимаются
// списком. Снятые уходят в отрицательный фильтр поиска.
//
// Сейчас названия добавляются вручную. Когда появится опознание через
// ИИ-агента, он будет складывать сюда кандидатов — разметка та же.

import { useState } from "react";

interface Props {
  label: string;
  hint: string;
  /** Предложенные кандидаты (например, найденные ИИ-агентом). */
  candidates?: string[];
  /** Отмеченные названия. */
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}

export default function NameCandidates({
  label,
  hint,
  candidates = [],
  value,
  onChange,
  placeholder,
}: Props) {
  const [draft, setDraft] = useState("");

  // Кандидаты и уже отмеченные имена показываются одним списком, иначе
  // добавленное вручную название визуально теряется.
  const rows = [...candidates];
  for (const name of value) {
    if (!rows.some((item) => item.toLowerCase() === name.toLowerCase())) {
      rows.push(name);
    }
  }

  const toggle = (name: string) =>
    onChange(
      value.some((item) => item.toLowerCase() === name.toLowerCase())
        ? value.filter((item) => item.toLowerCase() !== name.toLowerCase())
        : [...value, name],
    );

  const add = () => {
    const name = draft.trim();
    if (!name) return;
    if (!value.some((item) => item.toLowerCase() === name.toLowerCase())) {
      onChange([...value, name]);
    }
    setDraft("");
  };

  return (
    <div className="field">
      <label>{label}</label>
      {rows.length > 0 && (
        <div className="checks">
          {rows.map((name) => (
            <label key={name}>
              <input
                type="checkbox"
                checked={value.some(
                  (item) => item.toLowerCase() === name.toLowerCase(),
                )}
                onChange={() => toggle(name)}
              />
              {name}
            </label>
          ))}
        </div>
      )}
      <div className="row">
        <input
          value={draft}
          placeholder={placeholder ?? "добавить название"}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
        />
        <button type="button" onClick={add} disabled={!draft.trim()}>
          Добавить
        </button>
      </div>
      <span className="note">{hint}</span>
    </div>
  );
}
