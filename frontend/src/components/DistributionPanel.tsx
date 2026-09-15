/**
 * Is the work reaching anyone? -- the question the rest of Analytics cannot ask.
 *
 * Every other panel on the page divides by posts, so its best answer is "this photo
 * did better than that one". That silently assumes the audience is a constant and the
 * photograph is the variable. These four divide by audience instead, which is what
 * separates "nobody saw it" from "they saw it and didn't care" -- two diagnoses with
 * opposite remedies that the page previously rendered identically.
 *
 * The headline pair is deliberate: reach rate next to like rate. A low reach rate
 * beside a healthy like rate is the signature of a distribution problem, and reading
 * either number alone invites the wrong fix.
 *
 * Every figure carries its sample size and dims below MIN_SAMPLE, because at ~25
 * Instagram posts most of this is indicative rather than settled, and a confident-
 * looking number is how an indicative one gets acted on as fact.
 */
import { useQuery } from "@tanstack/react-query";

import { fetchDistribution } from "../api/client";
import { CardHeader } from "./PageHeader";

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v}%`;
}

function Stat({
  label,
  value,
  sub,
  thin,
  emphasis,
}: {
  label: string;
  value: string;
  sub?: string;
  thin?: boolean;
  emphasis?: boolean;
}) {
  return (
    <div style={{ display: "grid", gap: 2, opacity: thin ? 0.55 : 1 }}>
      <div
        style={{
          fontSize: 10.5,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--text-fade)",
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: emphasis ? 24 : 18,
          color: emphasis ? "var(--teal)" : "var(--text)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: "var(--text-fade)" }}>{sub}</div>}
    </div>
  );
}

export default function DistributionPanel({
  platform = "instagram",
  window: win = "7d",
}: {
  platform?: string;
  window?: string;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["distribution", platform, win],
    queryFn: () => fetchDistribution(platform, win),
  });

  if (isLoading) {
    return (
      <div className="fp-card" style={{ marginBottom: 16 }}>
        <CardHeader title="Reach & distribution" />
        <div style={{ padding: 16, fontSize: 13, color: "var(--text-fade)" }}>Loading…</div>
      </div>
    );
  }
  if (!data) return null;

  const { rates, decay, conversion, cadence } = data;

  return (
    <div className="fp-card" style={{ marginBottom: 16 }}>
      <CardHeader
        title="Reach & distribution"
        subtitle="Divided by audience, not by posts — this is what tells “nobody saw it” apart from “they saw it and scrolled past”."
      />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 20,
          padding: "4px 2px 16px",
        }}
      >
        <Stat
          label="Reach rate"
          value={pct(rates.median_reach_rate)}
          sub={`median, of followers at post time · n=${rates.posts}`}
          thin={rates.low_sample}
          emphasis
        />
        <Stat
          label="Like rate"
          value={pct(rates.median_like_rate)}
          sub="of the people actually reached"
          thin={rates.low_sample}
          emphasis
        />
        <Stat
          label="Reach by 24h"
          value={pct(decay.pct_by_24h)}
          sub={`of the 7-day total · n=${decay.n_24h}`}
          thin={decay.low_sample}
        />
        <Stat
          label="Reach by 48h"
          value={pct(decay.pct_by_48h)}
          sub={`judge a post from here · n=${decay.n_48h}`}
          thin={decay.low_sample}
        />
        <Stat
          label="Visit → follow"
          value={pct(conversion.conversion_pct)}
          sub={`${conversion.median_profile_views ?? "—"} profile views/day · ${conversion.days}d`}
          thin={conversion.low_sample}
        />
      </div>

      {/* Cadence is a comparison, so it gets a sentence rather than a tile: two medians
          side by side invite a causal reading the data cannot support. */}
      <div
        style={{
          borderTop: "0.5px solid var(--border)",
          paddingTop: 12,
          fontSize: 12,
          color: "var(--text-dim)",
          opacity: cadence.low_sample ? 0.6 : 1,
        }}
      >
        Days with one post reached{" "}
        <strong style={{ color: "var(--text)" }}>
          {pct(cadence.median_reach_rate_single)}
        </strong>{" "}
        ({cadence.single_post_days} days); posts on days carrying more than one reached{" "}
        <strong style={{ color: "var(--text)" }}>
          {pct(cadence.median_reach_rate_multi)}
        </strong>{" "}
        ({cadence.multi_post_posts} posts).{" "}
        <span style={{ color: "var(--text-fade)" }}>
          Not a controlled comparison — the days differ in more than post count.
        </span>
      </div>

      {rates.best.length > 0 && (
        <div style={{ overflowX: "auto", marginTop: 14 }}>
          <table
            style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 460 }}
          >
            <thead>
              <tr>
                {["Best reach", "Reach", "% of followers", "Like rate"].map((h, i) => (
                  <th
                    key={h}
                    style={{
                      textAlign: i === 0 ? "left" : "right",
                      padding: "6px 10px",
                      fontSize: 10.5,
                      letterSpacing: "0.06em",
                      textTransform: "uppercase",
                      color: "var(--text-fade)",
                      borderBottom: "0.5px solid var(--border)",
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rates.best.map((p) => (
                <tr key={p.post_id}>
                  <td
                    style={{
                      padding: "6px 10px",
                      maxWidth: 260,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={p.title ?? ""}
                  >
                    {p.title || "(untitled)"}
                  </td>
                  <td style={{ padding: "6px 10px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {p.reach}
                  </td>
                  <td style={{ padding: "6px 10px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {pct(p.reach_rate)}
                  </td>
                  <td style={{ padding: "6px 10px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {pct(p.like_rate)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
