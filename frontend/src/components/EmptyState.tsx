import type { ReactNode } from "react";

type Props = {
  icon?: ReactNode;
  title?: string;
  body?: ReactNode;
  action?: ReactNode;
};

export default function EmptyState({
  icon,
  title = "Your queue is clear",
  body = "Drop photos into the upload zone above, or wait for new exports to land in your watch folder.",
  action,
}: Props) {
  return (
    <div className="fp-card fp-fade-in">
      <div className="fp-empty">
        <div className="fp-empty-icon">{icon ?? <DefaultIcon />}</div>
        <div className="fp-empty-title">{title}</div>
        <div className="fp-empty-body">{body}</div>
        {action && <div style={{ marginTop: 16 }}>{action}</div>}
      </div>
    </div>
  );
}

function DefaultIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="12" cy="12" r="3.5" />
      <path d="M8 5l1.5-2h5L16 5" />
    </svg>
  );
}
