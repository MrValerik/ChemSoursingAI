import {
  forwardRef,
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";

const classes = (...values: Array<string | undefined | false>) =>
  values.filter(Boolean).join(" ");

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={classes("ui-control", className)} {...props} />;
  },
);

export interface SelectOption {
  value: string;
  label: string;
}

// Свой список вместо нативного: раскрывающуюся часть <select> рисует
// операционная система, её нельзя оформить и она выпадает из остального
// интерфейса. Клавиатура повторяет поведение нативного: стрелки ходят по
// пунктам, Enter выбирает, Esc закрывает и возвращает фокус на кнопку.
export function Select({
  value,
  options,
  onChange,
  className,
  disabled,
  ariaLabel,
  title,
}: {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  className?: string;
  disabled?: boolean;
  ariaLabel?: string;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const selectedIndex = options.findIndex((option) => option.value === value);
  const current = selectedIndex >= 0 ? options[selectedIndex] : undefined;

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const show = () => {
    setCursor(selectedIndex >= 0 ? selectedIndex : 0);
    setOpen(true);
  };

  const pick = (option: SelectOption) => {
    onChange(option.value);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const onKeyDown = (event: ReactKeyboardEvent) => {
    if (disabled) return;
    if (!open) {
      if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        show();
      }
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((i) => Math.min(i + 1, options.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((i) => Math.max(i - 1, 0));
    } else if (event.key === "Home") {
      event.preventDefault();
      setCursor(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setCursor(options.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (options[cursor]) pick(options[cursor]);
    }
  };

  return (
    <div className={classes("ui-select", className)} ref={rootRef} onKeyDown={onKeyDown}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        className="ui-select-trigger"
        disabled={disabled}
        ref={triggerRef}
        title={title}
        type="button"
        onClick={() => (open ? setOpen(false) : show())}
      >
        <span>{current?.label ?? ""}</span>
        <Icon name="chevron-down" size={15} className="ui-select-chevron" />
      </button>
      {open && (
        <div className="ui-select-menu" role="listbox" tabIndex={-1}>
          {options.map((option, index) => (
            <button
              aria-selected={option.value === value}
              className={classes(
                "ui-select-option",
                index === cursor && "is-cursor",
                option.value === value && "is-selected",
              )}
              key={option.value}
              role="option"
              type="button"
              onMouseEnter={() => setCursor(index)}
              onClick={() => pick(option)}
            >
              <span>{option.label}</span>
              {option.value === value && <Icon name="check" size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return <textarea ref={ref} className={classes("ui-control", className)} {...props} />;
});

export function Field({
  label,
  hint,
  error,
  className,
  children,
}: {
  label: ReactNode;
  hint?: string;
  error?: string | null;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label className={classes("ui-field", className)}>
      <span className="ui-field-label">{label}</span>
      {children}
      {hint && !error && <span className="ui-field-hint">{hint}</span>}
      {error && <span className="ui-field-error">{error}</span>}
    </label>
  );
}

export type IconName =
  | "bell"
  | "check"
  | "chevron-down"
  | "close"
  | "help"
  | "search"
  | "flask"
  | "edit"
  | "refresh"
  | "save"
  | "trash"
  | "sun"
  | "moon"
  | "lock";

export function Icon({
  name,
  size = 18,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  const paths: Record<IconName, ReactNode> = {
    bell: (
      <>
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
        <path d="M10 21h4" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
    sun: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" />
      </>
    ),
    moon: <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5" />,
    lock: (
      <>
        <rect x="5" y="10.5" width="14" height="9.5" rx="2.5" />
        <path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3" />
      </>
    ),
    "chevron-down": <path d="m6 9 6 6 6-6" />,
    close: (
      <>
        <path d="m7 7 10 10" />
        <path d="M17 7 7 17" />
      </>
    ),
    help: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M9.8 9a2.4 2.4 0 1 1 3.6 2.1c-.9.5-1.4 1.1-1.4 2" />
        <path d="M12 17h.01" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-4-4" />
      </>
    ),
    flask: (
      <>
        <path d="M9 3h6" />
        <path d="M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3" />
        <path d="M8 15h8" />
      </>
    ),
    edit: (
      <>
        <path d="M4 20h4l11-11-4-4L4 16v4Z" />
        <path d="m13.5 6.5 4 4" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 7v5h-5" />
        <path d="M4 17v-5h5" />
        <path d="M18.4 9A7 7 0 0 0 6.2 6.5L4 9" />
        <path d="M5.6 15A7 7 0 0 0 17.8 17.5L20 15" />
      </>
    ),
    save: (
      <>
        <path d="M5 4h12l2 2v14H5Z" />
        <path d="M8 4v6h8V4" />
        <path d="M8 20v-6h8v6" />
      </>
    ),
    trash: (
      <>
        <path d="M4 7h16" />
        <path d="M9 7V4h6v3" />
        <path d="m6 7 1 14h10l1-14" />
        <path d="M10 11v6" />
        <path d="M14 11v6" />
      </>
    ),
  };

  return (
    <svg
      aria-hidden="true"
      className={classes("ui-icon", className)}
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
    >
      <g stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7">
        {paths[name]}
      </g>
    </svg>
  );
}

export function IconButton({
  icon,
  label,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: IconName;
  label: string;
}) {
  return (
    <button
      {...props}
      aria-label={label}
      className={classes("ui-icon-button", className)}
      title={props.title ?? label}
      type={props.type ?? "button"}
    >
      <Icon name={icon} />
    </button>
  );
}

export function HelpTip({ text }: { text: string }) {
  return (
    <span className="help-tip">
      <button aria-label={text} type="button">
        <Icon name="help" size={14} />
      </button>
      <span role="tooltip">{text}</span>
    </span>
  );
}

export function Term({
  label,
  help,
}: {
  label: ReactNode;
  help: string;
}) {
  // Подсказка по наведению на само слово: без иконок, которые ломают
  // выравнивание при длинных значениях.
  return (
    <span className="term-help" tabIndex={0}>
      <span className="term-help-label">{label}</span>
      <span role="tooltip">{help}</span>
    </span>
  );
}

export function Toast({
  message,
  onClose,
  duration = 5000,
}: {
  message: string;
  onClose: () => void;
  duration?: number;
}) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const timer = window.setTimeout(() => onCloseRef.current(), duration);
    return () => window.clearTimeout(timer);
  }, [message, duration]);
  return (
    <div className="toast" role="status">
      <span className="toast-message">{message}</span>
      <button
        aria-label="Закрыть уведомление"
        className="toast-close"
        onClick={onClose}
        type="button"
      >
        <Icon name="close" size={14} />
      </button>
    </div>
  );
}

export interface MultiSelectOption {
  value: string;
  label: string;
}

export function MultiSelect({
  label,
  options,
  values,
  onChange,
  className,
}: {
  label: string;
  options: MultiSelectOption[];
  values: string[];
  onChange: (values: string[]) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const summary =
    values.length === 0
      ? `${label}: все`
      : values.length === 1
        ? `${label}: ${options.find((option) => option.value === values[0])?.label ?? values[0]}`
        : `${label}: выбрано ${values.length}`;

  const toggle = (value: string) => {
    onChange(
      values.includes(value)
        ? values.filter((item) => item !== value)
        : [...values, value],
    );
  };

  return (
    <div className={classes("multi-select", className)} ref={rootRef}>
      <button
        aria-expanded={open}
        className="multi-select-trigger"
        type="button"
        onClick={() => setOpen((current) => !current)}
      >
        <span>{summary}</span>
        <Icon name="chevron-down" size={15} />
      </button>
      {open && (
        <div className="multi-select-menu">
          <div className="multi-select-options">
            {options.map((option) => (
              <label key={option.value}>
                <input
                  checked={values.includes(option.value)}
                  type="checkbox"
                  onChange={() => toggle(option.value)}
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
          {values.length > 0 && (
            <button
              className="multi-select-clear"
              type="button"
              onClick={() => onChange([])}
            >
              Сбросить выбор
            </button>
          )}
        </div>
      )}
    </div>
  );
}
