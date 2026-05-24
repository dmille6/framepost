import { useMutation } from "@tanstack/react-query";

import { aiSuggestForPost, type AISuggestion, ApiError } from "../api/client";

type Props = {
  postId: string;
  enabled: boolean;
  currentTags: string;
  currentDescription: string;
  currentTitle: string;
  onAddTag: (tag: string) => void;
  onAddTags: (tags: string[]) => void;
  onUseDescription: (text: string) => void;
};

export default function AISuggestPanel({
  postId,
  enabled,
  currentTags,
  currentDescription,
  currentTitle,
  onAddTag,
  onAddTags,
  onUseDescription,
}: Props) {
  const suggest = useMutation({
    mutationFn: () =>
      aiSuggestForPost(postId, {
        hint_title: currentTitle.trim() || null,
        hint_tags: currentTags.trim() || null,
        hint_description: currentDescription.trim() || null,
      }),
  });

  const data: AISuggestion | undefined = suggest.data;
  const error =
    suggest.error instanceof ApiError ? suggest.error.message : suggest.error?.message;

  const currentTagsLower = new Set(
    currentTags.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean),
  );

  return (
    <div
      style={{
        background: "var(--bg)",
        border: "0.5px solid var(--border)",
        borderRadius: 8,
        padding: 12,
        display: "grid",
        gap: 10,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <span style={{ fontSize: 12, color: "var(--text-dim)" }}>AI suggestions</span>
          {data && (
            <span style={{ fontSize: 11, color: "var(--text-fade)", marginLeft: 8 }}>
              · {data.provider}
              {data.full_resolution ? " · full-res" : ""}
            </span>
          )}
        </div>
        <button
          className="fp-btn-ghost"
          style={{ padding: "4px 10px", fontSize: 12 }}
          disabled={!enabled || suggest.isPending}
          onClick={() => suggest.mutate()}
          title={enabled ? "Run the AI suggester on this image" : "Enable AI tagging in Settings → AI Tagging"}
        >
          {suggest.isPending ? "Asking…" : data ? "Re-suggest" : "Suggest"}
        </button>
      </div>

      {!enabled && (
        <div style={{ fontSize: 11, color: "var(--text-fade)" }}>
          AI tagging is off. Toggle it on in Settings → AI Tagging to use this.
        </div>
      )}

      {error && (
        <div style={{ fontSize: 12, color: "var(--danger)" }}>{error}</div>
      )}

      {data && (
        <>
          {data.tags.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--text-fade)" }}>No tags suggested.</div>
          ) : (
            <>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {data.tags.map((tag, i) => {
                  const sources = data.sources?.[i] ?? null;
                  const already = currentTagsLower.has(tag.toLowerCase());
                  return (
                    <button
                      key={tag}
                      onClick={() => !already && onAddTag(tag)}
                      disabled={already}
                      title={
                        already
                          ? "Already in your tags"
                          : sources
                            ? `From: ${sources.join(", ")} — click to add`
                            : "Click to add"
                      }
                      style={{
                        background: already ? "transparent" : pillBg(sources),
                        color: already ? "var(--text-fade)" : pillFg(sources),
                        border: already
                          ? "0.5px dashed var(--border-strong)"
                          : "0.5px solid transparent",
                        borderRadius: 999,
                        padding: "3px 10px",
                        fontSize: 12,
                        cursor: already ? "default" : "pointer",
                        textDecoration: already ? "line-through" : "none",
                      }}
                    >
                      {tag}
                      {sources && sources.length > 1 && (
                        <span style={{ marginLeft: 6, opacity: 0.8, fontSize: 10 }}>★</span>
                      )}
                      {sources && sources.length === 1 && data.provider === "both" && (
                        <span style={{ marginLeft: 6, opacity: 0.6, fontSize: 10 }}>
                          {sources[0] === "anthropic" ? "A" : "O"}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="fp-link"
                  onClick={() => onAddTags(data.tags.filter((t) => !currentTagsLower.has(t.toLowerCase())))}
                  style={{ fontSize: 12 }}
                >
                  Add all new
                </button>
                {data.provider === "both" && (
                  <button
                    className="fp-link"
                    onClick={() =>
                      onAddTags(
                        data.tags.filter(
                          (t, i) =>
                            (data.sources?.[i]?.length ?? 0) > 1 &&
                            !currentTagsLower.has(t.toLowerCase()),
                        ),
                      )
                    }
                    style={{ fontSize: 12 }}
                  >
                    Add agreed-by-both only ★
                  </button>
                )}
              </div>
            </>
          )}

          {data.description && (
            <div
              style={{
                marginTop: 4,
                paddingTop: 8,
                borderTop: "0.5px solid var(--border)",
                fontSize: 12,
              }}
            >
              <div style={{ color: "var(--text-fade)", marginBottom: 4 }}>Suggested caption</div>
              <div style={{ color: "var(--text-dim)", marginBottom: 6 }}>{data.description}</div>
              <button
                className="fp-link"
                onClick={() => onUseDescription(data.description!)}
                disabled={data.description === currentDescription}
                style={{ fontSize: 12 }}
              >
                {data.description === currentDescription ? "Already using this" : "Use this"}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function pillBg(sources: string[] | null): string {
  if (!sources) return "var(--hover)";
  if (sources.length > 1) return "rgba(93,202,165,0.18)"; // teal-tinted = agreed
  if (sources[0] === "anthropic") return "rgba(180,150,255,0.12)";
  return "rgba(120,180,255,0.12)";
}

function pillFg(sources: string[] | null): string {
  if (!sources) return "var(--text)";
  if (sources.length > 1) return "var(--teal)";
  return "var(--text)";
}
