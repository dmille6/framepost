/**
 * Whether a reel goes out as an Instagram Trial Reel, and how it graduates.
 *
 * A Trial Reel is shown only to people who don't follow the account; it reaches
 * followers only when it graduates. On an account where ~91% of followers never see a
 * given post, reaching new people is the point — but the photographer needs to know,
 * at the moment of choosing, that followers won't see it unless it graduates. So the
 * copy says that plainly, and what each graduation choice means.
 *
 * Trials go out without collaborator invites (see instagram.post_reel), which is the
 * other thing worth saying out loud: a performer credited on every normal reel isn't
 * invited on a trial. Their @handle is still in the caption.
 */
import type { TrialGraduation } from "../api/client";

export function ReelTrialField({
  value,
  onChange,
  disabled,
}: {
  value: TrialGraduation | null;
  onChange: (v: TrialGraduation | null) => void;
  disabled?: boolean;
}) {
  return (
    <div style={{ display: "grid", gap: 6, fontSize: 12 }}>
      <label style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={value !== null}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked ? "SS_PERFORMANCE" : null)}
        />
        <span>
          Trial Reel <span style={{ color: "var(--text-dim)" }}>— shown to non-followers first</span>
        </span>
      </label>
      {value !== null && (
        <div style={{ display: "grid", gap: 6, paddingLeft: 24 }}>
          <select
            className="fp-select"
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(e.target.value as TrialGraduation)}
            style={{ fontSize: 12, maxWidth: 360 }}
          >
            <option value="SS_PERFORMANCE">Share with followers automatically if it does well</option>
            <option value="MANUAL">I'll share it with followers in the Instagram app</option>
          </select>
          <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
            Followers won't see it unless it's shared with them. No collaborator
            invites on a trial — performers' @handles stay in the caption.
          </div>
        </div>
      )}
    </div>
  );
}

export function TrialBadge() {
  return (
    <span
      title="Instagram Trial Reel — shown to non-followers first"
      style={{
        fontSize: 10,
        fontWeight: 500,
        padding: "1px 6px",
        borderRadius: 4,
        background: "var(--amber-tint)",
        color: "var(--amber)",
        border: "0.5px solid rgba(240, 201, 122, 0.35)",
        whiteSpace: "nowrap",
      }}
    >
      Trial
    </span>
  );
}
