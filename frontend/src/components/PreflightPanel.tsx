/**
 * What will go wrong with this post, said before it is scheduled.
 *
 * The server has computed per-destination blockers and warnings on every draft row
 * since services/preflight.py landed, and the queue threw nearly all of it away: a
 * count, a filter toggle, a sort key, and a `title=` tooltip on the card. The one
 * screen where a blocker can actually be fixed -- this editor -- never saw it, and
 * the Schedule button was gated on `dirty` alone, so a post with Instagram
 * disconnected scheduled cleanly and failed hours later, out of sight.
 *
 * Findings are grouped by destination because that is how they are acted on: "Instagram
 * is disconnected" is one trip to Settings that clears every Instagram row at once.
 * Post-wide findings (no title, missing file) group under "This post".
 *
 * Deliberately dumb about freshness. `preflight` arrives with the post and goes stale
 * the moment the photographer types; rather than recompute in the browser -- a second
 * implementation of the rules, free to disagree with the server's -- the panel says so
 * and leans on the existing save-then-schedule order to refresh it.
 */
import type { Preflight, PreflightFinding } from "../api/client";

const LABELS: Record<string, string> = {
  flickr: "Flickr",
  instagram: "Instagram",
  bluesky: "Bluesky",
  pixelfed: "Pixelfed",
  pinterest: "Pinterest",
  reddit: "Reddit",
};

/** Findings in destination order, post-wide ones first. Exported for tests. */
export function groupByDestination(
  findings: PreflightFinding[],
): { destination: string | null; findings: PreflightFinding[] }[] {
  const order: (string | null)[] = [];
  const bins = new Map<string, PreflightFinding[]>();
  for (const f of findings) {
    // null and "" both mean post-wide; normalise so they share one bin.
    const key = f.destination || "";
    if (!bins.has(key)) {
      bins.set(key, []);
      order.push(f.destination || null);
    }
    bins.get(key)!.push(f);
  }
  // Post-wide first: it is the group whose fixes unblock every destination at once.
  order.sort((a, b) => (a === null ? -1 : b === null ? 1 : 0));
  return order.map((d) => ({ destination: d, findings: bins.get(d || "")! }));
}

export function destinationLabel(destination: string | null): string {
  if (!destination) return "This post";
  return LABELS[destination] ?? destination.charAt(0).toUpperCase() + destination.slice(1);
}

/** One-line summary for the collapsed/ready state. Exported for tests. */
export function summaryLine(pf: Preflight): string {
  const b = pf.blockers.length;
  const w = pf.warnings.length;
  if (b === 0 && w === 0) return "Ready to publish everywhere.";
  const parts: string[] = [];
  if (b) parts.push(`${b} blocker${b === 1 ? "" : "s"}`);
  if (w) parts.push(`${w} warning${w === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

type Props = {
  preflight: Preflight | null | undefined;
  /** True when the editor has unsaved edits, so the findings below predate them. */
  stale?: boolean;
};

export default function PreflightPanel({ preflight, stale = false }: Props) {
  // undefined means the endpoint didn't attach it (the editor is being used somewhere
  // other than the draft queue). Saying nothing beats inventing a verdict.
  if (!preflight) return null;

  const all = [...preflight.blockers, ...preflight.warnings];
  const clean = all.length === 0;
  const accent = preflight.blockers.length
    ? "var(--danger)"
    : preflight.warnings.length
      ? "var(--amber, #e0b268)"
      : "var(--teal)";

  return (
    <section
      aria-label="Preflight"
      style={{
        border: "0.5px solid var(--border-strong)",
        borderLeft: `2px solid ${accent}`,
        borderRadius: 8,
        padding: "10px 12px",
        display: "grid",
        gap: clean ? 0 : 10,
        background: "var(--surface-2, transparent)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
        <span style={{ color: accent, fontWeight: 500 }}>
          {clean ? "✓" : preflight.blockers.length ? "✕" : "!"}
        </span>
        <span style={{ color: "var(--text)" }}>{summaryLine(preflight)}</span>
        {stale && (
          <span
            style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-fade)" }}
            title="These checks ran before your current edits. Save to re-run them."
          >
            before unsaved edits
          </span>
        )}
      </div>

      {!clean && (
        <div style={{ display: "grid", gap: 8 }}>
          {groupByDestination(all).map(({ destination, findings }) => (
            <div key={destination ?? "_post"} style={{ display: "grid", gap: 3 }}>
              <div
                style={{
                  fontSize: 11,
                  color: "var(--text-dim)",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                {destinationLabel(destination)}
              </div>
              {findings.map((f, i) => (
                <div
                  key={`${f.code}-${i}`}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 8,
                    fontSize: 12,
                    color: f.level === "blocker" ? "var(--text)" : "var(--text-dim)",
                    lineHeight: 1.45,
                  }}
                >
                  <span
                    aria-hidden
                    style={{
                      color: f.level === "blocker" ? "var(--danger)" : "var(--amber, #e0b268)",
                      flexShrink: 0,
                    }}
                  >
                    {f.level === "blocker" ? "✕" : "!"}
                  </span>
                  <span>{f.message}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
