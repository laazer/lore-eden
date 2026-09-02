import { useEffect, useRef, type ReactNode, type RefObject } from "react";

import { useAnchoredPanelPosition } from "../hooks/useAnchoredPanelPosition";
import type { ComposerMenuItem } from "./commands";
import "./ComposerCommands.css";

/**
 * The completion list a `/` or `@` opens next to the composer.
 *
 * Presentational: it renders whatever `useComposerCommands` matched and reports
 * clicks back. Keyboard navigation lives with the input, because the input
 * keeps focus the whole time — this list is never tabbed into.
 *
 * Positioned against the input rather than by CSS: the same composer sits at
 * the bottom of the screen in the action bar and halfway up it in the chat
 * hero, and a menu pinned above would run off the top in the second case.
 */
export function ComposerCommandMenu({
  items,
  activeIndex,
  triggerKind,
  anchorRef,
  onHover,
  onPick,
  renderItem,
}: {
  items: ComposerMenuItem[];
  activeIndex: number;
  triggerKind: "slash" | "mention" | null;
  anchorRef: RefObject<HTMLInputElement | HTMLTextAreaElement | null>;
  onHover: (index: number) => void;
  onPick: (item: ComposerMenuItem) => void;
  /**
   * How one row reads, when a host wants more than a label and a detail line.
   *
   * The version this came from rendered command aliases, a "skill" tag and file
   * paths inline — one app's vocabulary hardcoded into the menu. The shell here
   * owns anchoring, keyboard navigation and open/close; what a row *says* is
   * the host's.
   */
  renderItem?: (item: ComposerMenuItem) => ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const open = Boolean(items.length && triggerKind);
  const style = useAnchoredPanelPosition(open, anchorRef, panelRef, {
    align: "left",
    matchWidth: true,
  });

  // Arrowing past the fold has to bring the highlight with it, or the list
  // scrolls out from under the selection and Enter picks something unseen.
  useEffect(() => {
    if (!open) return;
    panelRef.current
      ?.querySelectorAll(".lg-composer-menu-item")
      // `scrollIntoView?.` because jsdom has no implementation of it.
      [activeIndex]?.scrollIntoView?.({ block: "nearest" });
  }, [open, activeIndex]);

  if (!open) return null;

  return (
    <div
      ref={panelRef}
      className="lg-composer-menu"
      // Hidden until measured: an unpositioned first paint lands in the corner
      // of the screen and jumps.
      style={style ?? { position: "fixed", visibility: "hidden" }}
      role="listbox"
      aria-label={triggerKind === "slash" ? "Commands and skills" : "Files and folders"}
    >
      <div className="lg-composer-menu-hint">
        {triggerKind === "slash" ? "Commands · skills" : "Files · folders"}
        <span className="lg-composer-menu-keys">↑↓ · ⏎</span>
      </div>
      {items.map((item, index) => (
        <button
          key={item.id}
          type="button"
          role="option"
          aria-selected={index === activeIndex}
          className={`lg-composer-menu-item${index === activeIndex ? " is-active" : ""}`}
          // Mouse down, not click: a click fires after blur, and blurring the
          // composer closes the menu out from under the pointer.
          onMouseDown={(event) => {
            event.preventDefault();
            onPick(item);
          }}
          onMouseEnter={() => onHover(index)}
        >
          {renderItem === undefined ? (
            <>
              <span className="lg-composer-menu-name">{item.label}</span>
              {item.detail === undefined ? null : (
                <span className="lg-composer-menu-summary">{item.detail}</span>
              )}
            </>
          ) : (
            renderItem(item)
          )}
        </button>
      ))}
    </div>
  );
}
