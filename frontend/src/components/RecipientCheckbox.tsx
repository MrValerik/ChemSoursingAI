import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** Keeps unavailable choices focusable so their explanation is accessible. */
export default function RecipientCheckbox({
  checked, label, reason, onChange,
}: {
  checked: boolean;
  label: string;
  reason: string | null;
  onChange: () => void;
}) {
  const id = useId();
  const ref = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const show = () => {
    if (!reason || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    setPosition({
      left: Math.max(12, Math.min(rect.left, window.innerWidth - 332)),
      top: Math.max(12, Math.min(rect.bottom + 6, window.innerHeight - 180)),
    });
  };
  useEffect(() => {
    const hide = () => setPosition(null);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, []);
  return (
    <>
      <button
        ref={ref}
        type="button"
        role="checkbox"
        className="recipient-checkbox"
        aria-checked={checked}
        aria-disabled={!!reason}
        aria-label={label}
        aria-describedby={reason ? id : undefined}
        onMouseEnter={show}
        onMouseLeave={() => setPosition(null)}
        onFocus={show}
        onBlur={() => setPosition(null)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setPosition(null);
        }}
        onClick={(event) => {
          event.stopPropagation();
          if (reason) show();
          else onChange();
        }}
      >
        <span className="recipient-checkbox-mark" aria-hidden="true">
          {checked ? (
            <svg viewBox="0 0 20 20"><path d="m4 10 4 4 8-8" /></svg>
          ) : reason ? (
            <svg viewBox="0 0 20 20"><path d="M6 9V6a4 4 0 0 1 8 0v3M5 9h10v8H5z" /></svg>
          ) : null}
        </span>
      </button>
      {reason && createPortal(
        <span
          id={id}
          role="tooltip"
          className={position ? "recipient-checkbox-tooltip" : "recipient-checkbox-description"}
          style={position ?? undefined}
        >{reason}</span>,
        document.body,
      )}
    </>
  );
}
