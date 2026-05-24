import type { CSSProperties } from "react";

type Props = {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  style?: CSSProperties;
};

export default function Skeleton({ width = "100%", height = 16, radius = 6, style }: Props) {
  return (
    <span
      className="fp-skeleton"
      style={{ width, height, borderRadius: radius, ...style }}
      aria-hidden
    />
  );
}

export function SkeletonCard({ height = 160 }: { height?: number }) {
  return (
    <div className="fp-card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Skeleton height={height} radius={8} />
      <Skeleton width="70%" height={14} />
      <Skeleton width="40%" height={12} />
    </div>
  );
}

export function SkeletonGrid({ count = 8, cardHeight = 180 }: { count?: number; cardHeight?: number }) {
  return (
    <div className="fp-grid-cards">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} height={cardHeight} />
      ))}
    </div>
  );
}

export function SkeletonRows({ count = 5, height = 44 }: { count?: number; height?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} height={height} radius={8} />
      ))}
    </div>
  );
}
