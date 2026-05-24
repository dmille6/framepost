import type { ReactNode } from "react";

type Props = {
  label: string;
  hint?: string;
  children: ReactNode;
};

export default function ConfigField({ label, hint, children }: Props) {
  return (
    <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
      <span>
        {label}
        {hint && <span style={{ marginLeft: 8, color: "var(--text-fade)" }}>{hint}</span>}
      </span>
      {children}
    </label>
  );
}
