import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** A small modal confirmation; Escape cancels, focus starts on Cancel. */
export default function Confirm({ title, children, confirmLabel, danger, onConfirm, onCancel }: Props) {
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => cancel.current?.focus(), []);
  return (
    <div className="backdrop" onKeyDown={(e) => e.key === "Escape" && onCancel()}>
      <div role="dialog" aria-modal="true" aria-label={title} className="dialog">
        <h2>{title}</h2>
        {children}
        <div className="dialog-buttons">
          <button type="button" ref={cancel} onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className={danger ? "danger" : "primary"} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
