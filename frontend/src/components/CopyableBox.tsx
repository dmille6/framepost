import { useEffect, useState, type CSSProperties, type ReactNode } from "react";

type Props = {
  /** Plain text to put on the clipboard. */
  text: string;
  /** What renders inside the box (typically a styled <pre> or <div>). */
  children: ReactNode;
  /** Extra style overrides on the clickable wrapper. */
  style?: CSSProperties;
};

/**
 * Click-to-copy box. The whole rectangle is the click target — much easier than aiming at a
 * tiny button — with a floating Copy button in the top-right that brightens on hover and a
 * green "Copied!" pill that fades after 1.5s. We deliberately don't render the children's
 * background ourselves; the caller's styled element supplies it. This keeps font / max-height /
 * overflow behavior owned by the caller.
 */
export default function CopyableBox({ text, children, style }: Props) {
  const [copied, setCopied] = useState(false);
  const [hover, setHover] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      // Fallback: attempt a deprecated execCommand path. If that fails too, surface nothing —
      // user can still select-and-copy manually from the rendered text below.
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        setCopied(true);
      } catch {
        /* noop */
      }
    }
  }

  return (
    <div
      onClick={handleCopy}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          void handleCopy();
        }
      }}
      style={{
        position: "relative",
        cursor: "pointer",
        outline: "none",
        ...style,
      }}
      title="Click to copy"
    >
      {children}
      <span
        aria-hidden
        style={{
          position: "absolute",
          top: 8,
          right: 8,
          fontSize: 11,
          fontWeight: 500,
          padding: "4px 8px",
          borderRadius: 6,
          background: copied
            ? "var(--teal)"
            : hover
              ? "rgba(255,255,255,0.08)"
              : "rgba(255,255,255,0.04)",
          color: copied ? "#0a1f17" : hover ? "var(--text)" : "var(--text-dim)",
          border: `0.5px solid ${copied ? "var(--teal)" : "var(--border-strong)"}`,
          transition: "background 120ms ease, color 120ms ease, border-color 120ms ease",
          pointerEvents: "none",
          backdropFilter: "blur(2px)",
        }}
      >
        {copied ? "✓ Copied" : "Copy"}
      </span>
    </div>
  );
}
