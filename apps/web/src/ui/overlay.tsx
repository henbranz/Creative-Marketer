"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { UIIcon } from "./icons";

/** Presentation only. Does not create authority, save data or trigger a domain action. */
export function Overlay({
  title,
  children,
  onClose,
  mode = "drawer",
  busy = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  mode?: "drawer" | "dialog";
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current!;
    const trigger =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const overflow = document.documentElement.style.overflow;
    dialog.showModal();
    (
      dialog.querySelector<HTMLElement>("[data-initial-focus]") ??
      dialog.querySelector<HTMLElement>("button")
    )?.focus();
    document.documentElement.style.overflow = "hidden";
    return () => {
      dialog.close();
      document.documentElement.style.overflow = overflow;
      if (trigger?.isConnected) trigger.focus();
    };
  }, []);
  const close = () => {
    if (!busy) onClose();
  };
  return (
    <dialog
      ref={ref}
      className={`cm-overlay ${mode}`}
      aria-labelledby={titleId}
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          const bounds = event.currentTarget.getBoundingClientRect();
          if (
            event.clientX < bounds.left ||
            event.clientX > bounds.right ||
            event.clientY < bounds.top ||
            event.clientY > bounds.bottom
          )
            close();
        }
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          close();
        }
        if (event.key !== "Tab") return;
        const items = [
          ...event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
          ),
        ].filter((element) => !element.hidden && !element.closest("[hidden]"));
        const first = items[0];
        const last = items.at(-1);
        if (!first) {
          event.preventDefault();
          event.currentTarget.focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
    >
      <div className="overlay-header">
        <h2 id={titleId}>{title}</h2>
        <button
          className="icon-button"
          onClick={close}
          disabled={busy}
          aria-label={`Close ${title}`}
        >
          <UIIcon name="close" />
        </button>
      </div>
      <div className="overlay-body" aria-busy={busy}>
        {children}
      </div>
    </dialog>
  );
}
