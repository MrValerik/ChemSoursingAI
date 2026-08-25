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

import { HelpTip } from "./ui";

interface Props {
  label: string;
  hint: string;
  /** Предложенные кандидаты (например, найденные ИИ-агентом). */
  candidates?: string[];
  /** Отмеченные названия. */
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  /**
   * Откуда взялось предложение и почему. Отмеченное название закупщик
   * увидит в письме поставщику, поэтому должен иметь возможность узнать,
   * кто за него отвечает: запись реестра или прочтение страницы моделью.
   * Названия без объяснения — добавленные вручную.
   */
  hintFor?: (name: string) => string | undefined;
}

export default function NameCandidates({
  label,
  hint,
  candidates = [],
  value,
  onChange,
  placeholder,
  hintFor,
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
      <span className="name-check-labelrow">
        <label>{label}</label>
        <HelpTip text={hint} />
      </span>
      {/* Сначала поле ввода, затем отмеченные названия: список растёт вниз
          от того места, где их добавляют, и добавленное имя оказывается
          прямо под курсором, а не уезжает выше поля. */}
      <div className="row name-check-add">
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
      {rows.length > 0 && (
        <div className="name-checks">
          {rows.map((name) => {
            const checked = value.some(
              (item) => item.toLowerCase() === name.toLowerCase(),
            );
            const explanation = hintFor?.(name);
            return (
              <label
                key={name}
                className={`name-check${checked ? " is-checked" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(name)}
                />
                <span className="name-check-box" aria-hidden="true" />
                <span className="name-check-text">{name}</span>
                {explanation && <HelpTip text={explanation} />}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
