import type { ReactNode } from "react";

type Props = {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
};

export default function PageHeader({ title, subtitle, actions }: Props) {
  return (
    <div className="fp-page-header">
      <div>
        <h1 className="fp-page-title">{title}</h1>
        {subtitle && <div className="fp-page-subtitle">{subtitle}</div>}
      </div>
      {actions && <div style={{ display: "flex", gap: 8 }}>{actions}</div>}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="fp-card-header">
      <div>
        <h2 className="fp-card-header-title">{title}</h2>
        {subtitle && <div className="fp-card-header-subtitle">{subtitle}</div>}
      </div>
      {action && <div>{action}</div>}
    </div>
  );
}
