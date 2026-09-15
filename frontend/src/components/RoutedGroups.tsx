/**
 * The Flickr groups this post will go to, as an answer rather than a checklist.
 *
 * ~25 pools, and 517 of 518 posts carry a stage/performance tag, so for most groups the
 * answer was always yes and the ticking was ceremony -- which is why
 * services/group_routing.py started deciding it from the tags. The editor never caught
 * up: it still rendered all 25 checkboxes, capped at five by a constant, so the feature
 * built to end the clicking was invisible beside the clicking it replaced.
 *
 * Routing is previewed against the tags currently typed, not the saved ones, because
 * that is the moment the photographer is deciding. The preview calls the same
 * `accepts()` the publish path calls; it does not reimplement the rule.
 *
 * Manual selection still exists and still wins -- `groups_overridden` on the post
 * records that a human chose, and once set the publish-time seeder never touches it
 * again. That flag is why "override" is a door and not a one-way trip: the component
 * shows which side of it the post is on.
 */
import { useQuery } from "@tanstack/react-query";

import { previewGroupRouting, type RoutedGroup } from "../api/client";

type Props = {
  /** Live editor tag text, so the preview tracks what is being typed. */
  tags: string;
  /** True once the photographer has saved a manual selection for this post. */
  overridden: boolean;
  /** How many groups the manual selection holds, shown when overridden. */
  manualCount: number;
  /** Reveal the checkbox list. */
  onOverride: () => void;
  /** Hand routing back control; clears the manual selection upstream. */
  onUseRouting?: () => void;
};

/** Sentence describing a routed set. Exported for tests. */
export function routingSummary(groups: RoutedGroup[]): string {
  if (groups.length === 0) {
    return "No groups match these tags — add tags, or pick groups by hand.";
  }
  return `Routing to ${groups.length} group${groups.length === 1 ? "" : "s"}`;
}

export default function RoutedGroups({
  tags,
  overridden,
  manualCount,
  onOverride,
  onUseRouting,
}: Props) {
  // Keyed on the tag text: typing a tag re-runs the preview, which is the point.
  const { data: routed = [], isLoading } = useQuery({
    queryKey: ["group-routing", tags],
    queryFn: () => previewGroupRouting(tags),
    // Tags change on every keystroke; without this the endpoint is hit per character.
    staleTime: 30_000,
    enabled: !overridden,
  });

  if (overridden) {
    return (
      <div style={{ display: "grid", gap: 6, fontSize: 12 }}>
        <div style={{ color: "var(--text-dim)" }}>
          Groups chosen by hand ({manualCount}). Automatic routing is off for this post.
        </div>
        {onUseRouting && (
          <button
            type="button"
            className="fp-link"
            onClick={onUseRouting}
            style={{ fontSize: 12, justifySelf: "start" }}
          >
            Use automatic routing instead
          </button>
        )}
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 6, fontSize: 12 }}>
      <div style={{ color: "var(--text-dim)" }}>
        {isLoading ? "Checking group rules…" : routingSummary(routed)}
      </div>
      {routed.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {routed.map((g) => (
            <span
              key={g.id}
              title={
                g.matched.length
                  ? `Matched on: ${g.matched.join(", ")}`
                  : "Takes any subject — no tag rule"
              }
              style={{
                padding: "3px 10px",
                borderRadius: 999,
                border: "0.5px solid var(--border-strong)",
                color: "var(--text-dim)",
              }}
            >
              {g.name}
            </span>
          ))}
        </div>
      )}
      <button
        type="button"
        className="fp-link"
        onClick={onOverride}
        style={{ fontSize: 12, justifySelf: "start" }}
      >
        Choose groups by hand
      </button>
    </div>
  );
}
