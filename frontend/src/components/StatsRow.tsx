type Stat = { label: string; value: number | string; hint?: string };

export default function StatsRow({ stats }: { stats: Stat[] }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${stats.length}, minmax(0, 1fr))`,
        gap: 16,
        marginBottom: 24,
      }}
    >
      {stats.map((s) => (
        <div key={s.label} className="fp-card" style={{ padding: 16 }}>
          <div style={{ fontSize: 12, color: "var(--text-dim)", letterSpacing: 0.02 }}>
            {s.label}
          </div>
          <div style={{ fontSize: 28, fontWeight: 500, marginTop: 4 }}>{s.value}</div>
          {s.hint && (
            <div style={{ fontSize: 11, color: "var(--text-fade)", marginTop: 2 }}>{s.hint}</div>
          )}
        </div>
      ))}
    </div>
  );
}
