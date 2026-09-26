"use client";

import { RotateCcw } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

/**
 * A native modal <dialog>: it traps focus, closes on Escape, and hands focus back to
 * whatever had it. One primary action and a quiet way out, which gets focus first
 * (it comes first in the dialog) so a stray Enter never confirms.
 */
export function ConfirmDialog({
  open,
  question,
  context,
  confirmWord,
  dismissWord,
  onConfirm,
  onDismiss,
}: {
  open: boolean;
  question: string;
  /** A line above the question saying what it's about (a ticket). */
  context?: ReactNode;
  confirmWord: string;
  dismissWord: string;
  onConfirm: () => void;
  onDismiss: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      aria-labelledby={titleId}
      // Escape and a programmatic close both land here; only the first one needs telling.
      onClose={() => {
        if (open) onDismiss();
      }}
    >
      {context ? <p className="confirm-context">{context}</p> : null}
      <h2 className="confirm-question" id={titleId}>
        {question}
      </h2>
      <div className="confirm-actions">
        <button type="button" className="link-button" onClick={onDismiss}>
          {dismissWord}
        </button>
        <button type="button" className="button" onClick={onConfirm}>
          <RotateCcw size={16} strokeWidth={2.5} aria-hidden />
          {confirmWord}
        </button>
      </div>
    </dialog>
  );
}
