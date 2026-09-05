// Выбор базисов поставки.
//
// Раньше это был ряд карточек: код и под ним фраза о том, кто за что
// платит. На пяти базисах это читалось, на одиннадцати — стена текста,
// в которой не видно, что выбрано. Поэтому список открывается по
// нажатию, коды идут сеткой, а условие показывается по наведению — на
// одну строку внизу списка.
//
// Свой базис. Закупщик работает и по условиям, которых в редакции 2020
// нет: DDU из редакции 2000, самовывоз со склада, формулировка своего
// договора. Раньше их некуда было деть, кроме свободного комментария,
// где поставщик отвечает на них как захочет и предложения перестают
// сравниваться на одном базисе. Теперь такой базис вписывается прямо
// здесь — но места поставки программа ему не придумывает и честно
// говорит об этом: место поставщик подтверждает в ответе.

import { useEffect, useMemo, useRef, useState } from "react";

import {
  CUSTOM_INCOTERM_MAX_LENGTH,
  INCOTERM_HINTS,
  INCOTERM_OPTIONS,
  isReferenceIncoterm,
  normalizeCustomIncoterm,
} from "./incoterms";
import { Icon } from "./ui";

interface Props {
  values: string[];
  onChange: (values: string[]) => void;
  /** Разрешить базис вне справочника. */
  allowCustom?: boolean;
  /** Подпись для читалки экрана: у поля своя, у пакета — своя. */
  label?: string;
}

export default function IncotermPicker({
  values,
  onChange,
  allowCustom = true,
  label = "Условия поставки",
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // Код, чьё условие сейчас показано внизу списка: наведение мышью или
  // перемещение стрелками — для клавиатуры это одно и то же место.
  const [hovered, setHovered] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const filtered = useMemo(() => {
    const needle = query.trim().toUpperCase();
    if (!needle) return INCOTERM_OPTIONS;
    return INCOTERM_OPTIONS.filter((option) => option.code.includes(needle));
  }, [query]);

  // Свой базис предлагается только тогда, когда набранное не совпало с
  // кодом из справочника: иначе кнопка «добавить своё» соблазняла бы
  // вписать «CIP» руками мимо справочника.
  const custom = allowCustom ? normalizeCustomIncoterm(query) : null;
  const customOffered =
    custom !== null && !isReferenceIncoterm(custom) && !values.includes(custom);

  const toggle = (code: string) => {
    onChange(
      values.includes(code)
        ? values.filter((item) => item !== code)
        : [...values, code],
    );
  };

  const addCustom = () => {
    if (!customOffered || custom === null) return;
    onChange([...values, custom]);
    setQuery("");
    inputRef.current?.focus();
  };

  const close = () => {
    setOpen(false);
    setQuery("");
  };

  const openList = () => {
    setOpen(true);
    // Фокус на поле: список открыт — значит закупщик уже целится в код,
    // и набор должен попадать в фильтр, а не в никуда.
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      close();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (filtered.length === 1) toggle(filtered[0].code);
      else if (customOffered) addCustom();
      return;
    }
    if (event.key === "Backspace" && !query && values.length > 0) {
      // Пустое поле и Backspace: снимается последний выбранный — так
      // ведут себя поля с фишками везде, и отдельно объяснять это не надо.
      onChange(values.slice(0, -1));
    }
  };

  const hoveredHint = hovered
    ? (INCOTERM_HINTS[hovered] ??
      "Свой базис. Место поставки программа не назначает — его подтверждает поставщик в ответе.")
    : "";

  const hasCustom = values.some((code) => !isReferenceIncoterm(code));

  return (
    // Escape ловится на всём блоке, а не на поле ввода: после выбора кода
    // мышью фокус стоит на кнопке списка, и обработчик поля туда не
    // достаёт — список оставался открытым до щелчка мимо него.
    <div
      className="incoterm-picker"
      ref={rootRef}
      onKeyDown={(event) => {
        if (event.key === "Escape") close();
      }}
    >
      <div
        className={`incoterm-control${open ? " is-open" : ""}`}
        onClick={() => (open ? inputRef.current?.focus() : openList())}
      >
        {values.map((code) => (
          <span
            key={code}
            className={`incoterm-chip${isReferenceIncoterm(code) ? "" : " is-custom"}`}
            title={
              INCOTERM_HINTS[code] ??
              "Свой базис: место поставки подтверждает поставщик."
            }
          >
            {code}
            <button
              aria-label={`Убрать базис ${code}`}
              className="incoterm-chip-remove"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onChange(values.filter((item) => item !== code));
              }}
            >
              <Icon name="close" size={11} />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          aria-label={label}
          aria-expanded={open}
          className="incoterm-query"
          placeholder={
            values.length === 0
              ? "Нажмите и выберите базис"
              : allowCustom
                ? "Ещё базис или свой"
                : "Ещё базис"
          }
          role="combobox"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        <Icon name="chevron-down" size={15} className="incoterm-caret" />
      </div>

      {open && (
        <div className="incoterm-menu">
          <div className="incoterm-options">
            {filtered.map((option) => (
              <button
                key={option.code}
                aria-label={`${option.code} — ${option.hint}`}
                aria-pressed={values.includes(option.code)}
                className={`incoterm-option${values.includes(option.code) ? " is-picked" : ""}`}
                title={option.hint}
                type="button"
                onClick={() => toggle(option.code)}
                onFocus={() => setHovered(option.code)}
                onMouseEnter={() => setHovered(option.code)}
                onMouseLeave={() =>
                  setHovered((current) =>
                    current === option.code ? null : current,
                  )
                }
              >
                {option.code}
              </button>
            ))}
            {filtered.length === 0 && !customOffered && (
              <span className="incoterm-empty">
                Такого базиса в редакции 2020 нет.
                {allowCustom
                  ? " Впишите его целиком, чтобы добавить своим."
                  : " В файле со списком позиций свой базис не принимается."}
              </span>
            )}
          </div>

          {customOffered && (
            <button
              className="incoterm-add-custom"
              type="button"
              onClick={addCustom}
              onMouseEnter={() => setHovered(custom)}
              onMouseLeave={() => setHovered(null)}
            >
              Добавить свой базис «{custom}»
            </button>
          )}

          {/* Условие показывается здесь, а не под каждым кодом: одиннадцать
              фраз подряд превращают выбор в чтение. */}
          <p className="incoterm-explain">
            {hoveredHint || "Наведите на код — здесь появится, кто что делает."}
          </p>
        </div>
      )}

      {hasCustom && (
        <p className="note incoterm-custom-note">
          Свой базис уходит поставщику как есть: место поставки программа ему
          не назначает, и в письме поставщика просят это место подтвердить.
        </p>
      )}
      {allowCustom && (
        <p className="visually-hidden">
          Свой базис — до {CUSTOM_INCOTERM_MAX_LENGTH} знаков.
        </p>
      )}
    </div>
  );
}
