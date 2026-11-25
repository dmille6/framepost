import { useEffect, useRef, type MouseEvent } from "react";

// Two rules every dialog shares, kept in one place because each dialog used to carry
// its own copy and all of them were wrong in the same two ways.
//
// 1. A backdrop closes the dialog only for a click that STARTED on the backdrop.
//    `onClick` alone is not that: a browser sends a click to the nearest common ancestor
//    of where the button went down and where it came up. Press inside a text field, drag
//    to select, release a few pixels past the card, and the click lands on the backdrop
//    — the dialog closed and took the form with it. The card's stopPropagation never ran,
//    because the event never passed through the card.
//
// 2. Escape belongs to the topmost dialog only. Dialogs listened on `window`
//    independently, so one Escape inside a dialog opened from another closed both.

/** Spread on the backdrop element. Dismisses only when press and release both hit it. */
export function useBackdropDismiss(onDismiss: (() => void) | undefined) {
  const pressedOnBackdrop = useRef(false);
  return {
    onMouseDown: (e: MouseEvent) => {
      pressedOnBackdrop.current = e.target === e.currentTarget;
    },
    onClick: (e: MouseEvent) => {
      const genuine = pressedOnBackdrop.current && e.target === e.currentTarget;
      pressedOnBackdrop.current = false;
      if (genuine) onDismiss?.();
    },
  };
}

// Open dialogs, oldest first. A dialog opened from inside another mounts later, so it
// sits above its opener. Module-level on purpose: the stack is a fact about the page.
const layers: object[] = [];

/** Registers a dialog as a layer. `onEscape` runs only while this dialog is the top
 *  one. A dialog with no Escape behaviour should still register (pass nothing), so the
 *  dialog underneath stops answering keys meant for it. */
export function useModalLayer(onEscape?: () => void) {
  const handler = useRef(onEscape);
  handler.current = onEscape;
  const self = useRef<object | null>(null);

  useEffect(() => {
    const layer = {};
    self.current = layer;
    layers.push(layer);
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (layers[layers.length - 1] !== layer) return;
      handler.current?.();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      const i = layers.indexOf(layer);
      if (i >= 0) layers.splice(i, 1);
    };
  }, []);

  return {
    /** True while no other dialog is open above this one. */
    isTop: () => self.current !== null && layers[layers.length - 1] === self.current,
  };
}

const KEY_OWNERS =
  "input:not([type=button]):not([type=submit]):not([type=reset]):not([type=checkbox]):not([type=radio]), " +
  "textarea, select, [role=slider], [role=spinbutton], [role=listbox], [role=radiogroup], " +
  "[role=tablist], [role=menu], [role=grid], [role=tree], [role=combobox], " +
  "[contenteditable]:not([contenteditable='false'])";

/** True when the focused control uses arrow keys itself (text fields, sliders, lists),
 *  so a page-level arrow shortcut must leave the key alone. */
export function ownsArrowKeys(target: EventTarget | null): boolean {
  const t = target as HTMLElement | null;
  return !!(t?.isContentEditable || t?.closest?.(KEY_OWNERS));
}
