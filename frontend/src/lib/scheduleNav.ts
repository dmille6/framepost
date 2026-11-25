import type { ScheduledItem } from "../api/client";

type Entry = Pick<ScheduledItem, "id" | "scheduled_at" | "carousel_position">;

function cmp(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** The order the previous/next arrows walk: by scheduled time, then a carousel's frames
 *  in their own order (they share one time), then id — a fixed last tie-break, so two
 *  posts in the same minute never swap places between refetches. */
export function navOrder<T extends Entry>(items: T[]): T[] {
  return items
    .filter((i) => i.scheduled_at)
    .sort(
      (a, b) =>
        cmp(a.scheduled_at!, b.scheduled_at!) ||
        (a.carousel_position ?? 0) - (b.carousel_position ?? 0) ||
        cmp(a.id, b.id),
    );
}

/** Where `id` sits in an ordered list, and what is either side of it. `index` is -1
 *  when the entry is not in the list (it was unscheduled, or the list is still loading). */
export function neighbours<T extends Entry>(ordered: T[], id: string) {
  const index = ordered.findIndex((i) => i.id === id);
  return {
    index,
    total: ordered.length,
    prev: index > 0 ? ordered[index - 1] : null,
    next: index >= 0 && index < ordered.length - 1 ? ordered[index + 1] : null,
  };
}
