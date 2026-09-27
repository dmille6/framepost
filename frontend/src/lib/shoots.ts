// Grouping the queue by the shoot a frame came from, rather than by publish date.
//
// Why the filename and not the `show` field. `show` looks like the obvious key and is the
// wrong one: measured on a live 354-post queue it carried three spellings of one show
// ("VariTease", "Varietease", "Varitease") and both "Teaser Fest" and "Teaser Fest 2026",
// so keying on it split coherent shoots into several groups and left 300 of 461 pending
// posts with no key at all. Lightroom exports, by contrast, land named
// "2026-TeaserFest-Varitease (69 of 69).jpg" — stripping the counter yields a stable key
// for the whole shoot. On that same queue the filename key produced 34 groups with only
// two singletons.
//
// So `show` is used for the LABEL and never for the key: a group whose members mostly
// agree on a show name gets that name, which turns "Shot 2022-12-18" into "Worship".
//
// batchKey is deliberately identical to the one the Drafts page has always used, so the
// two pages group the same photos the same way. Drafts imports it from here.

export type ShootItem = {
  id: string;
  original_filename: string | null;
  captured_at?: string | null;
  show?: string | null;
  scheduled_at?: string | null;
};

/** Stable key for the shoot a frame belongs to, or null when nothing identifies one. */
export function batchKey(p: ShootItem): string | null {
  const base = (p.original_filename || "").replace(/\.[A-Za-z0-9]+$/, "").trim();
  const stripped = base
    .replace(/\s*[(\[]?\d+\s+of\s+\d+[)\]]?\s*$/i, "")
    .replace(/[-_\s]+$/, "")
    .trim();
  if (stripped && stripped !== base) return stripped;
  if (p.captured_at) {
    const d = new Date(p.captured_at);
    if (!Number.isNaN(d.getTime())) {
      return `Shot ${d.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      })}`;
    }
  }
  return null;
}

/** How much of a group must carry the same show name before it is used as the label.
 *  A third, not a majority: on the live queue a 20-frame shoot had `show` filled in on
 *  half its frames and a strict majority rejected it by one, falling back to "Shot Dec 18,
 *  2022" when "Worship" was right there. A third still ignores a single stray tag on a
 *  60-frame shoot, and the derived key stays visible as the subtitle either way, so a
 *  generous label never hides what the group actually is. */
const SHOW_LABEL_COVERAGE = 1 / 3;

/** The show name a group mostly agrees on, or null. */
function agreedShow(items: ShootItem[]): string | null {
  const counts = new Map<string, number>();
  for (const it of items) {
    const s = (it.show || "").trim();
    if (s) counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  if (counts.size === 0) return null;
  const [name, n] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
  return n >= items.length * SHOW_LABEL_COVERAGE ? name : null;
}

export type Shoot<T extends ShootItem> = {
  key: string;
  /** What to show as the heading — the agreed show name where there is one. */
  label: string;
  /** The derived key, present only when it differs from the label, so the filename batch
   *  stays visible rather than being hidden behind a prettier name. */
  subtitle: string | null;
  items: T[];
  /** Earliest and latest scheduled_at in the group, ISO, when any member has one. */
  firstAt: string | null;
  lastAt: string | null;
};

const UNGROUPED = "\u0000ungrouped";

/**
 * Group items into shoots.
 *
 * Ordering is by the soonest thing each shoot publishes, so the view still reads as a
 * queue rather than an archive; anything with no date sorts last. Items inside a shoot
 * stay in publish order.
 */
export function groupIntoShoots<T extends ShootItem>(items: T[]): Shoot<T>[] {
  const buckets = new Map<string, T[]>();
  for (const it of items) {
    const key = batchKey(it) ?? UNGROUPED;
    const list = buckets.get(key);
    if (list) list.push(it);
    else buckets.set(key, [it]);
  }

  type Draft = { key: string; show: string | null; derived: string; list: T[] };
  const drafts: Draft[] = [...buckets].map(([key, list]) => ({
    key,
    show: agreedShow(list),
    derived: key === UNGROUPED ? "Unmatched" : key,
    list,
  }));

  // A show name is only a better heading than the filename when it identifies ONE shoot.
  // One show is routinely shot several times over, once per performer: on the live queue
  // five separate shoots resolved to "Teaser Fest 2026", where the filenames said
  // LadyMidnight, ConradCrow and so on. Five identical headings are worse than five
  // specific ones, so a duplicated show name drops to the subtitle and the filename takes
  // the heading back. Either way both strings are on screen.
  const showUses = new Map<string, number>();
  for (const d of drafts) {
    if (d.show) showUses.set(d.show, (showUses.get(d.show) ?? 0) + 1);
  }

  const shoots: Shoot<T>[] = drafts.map((d) => {
    const dated = d.list
      .map((i) => i.scheduled_at)
      .filter((v): v is string => !!v)
      .sort();
    const useShow = !!d.show && showUses.get(d.show) === 1 && d.show !== d.derived;
    return {
      key: d.key,
      label: useShow ? (d.show as string) : d.derived,
      subtitle: useShow ? d.derived : d.show && d.show !== d.derived ? d.show : null,
      items: [...d.list].sort((a, b) =>
        (a.scheduled_at || "").localeCompare(b.scheduled_at || ""),
      ),
      firstAt: dated[0] ?? null,
      lastAt: dated[dated.length - 1] ?? null,
    };
  });

  return shoots.sort((a, b) => {
    if (a.key === UNGROUPED) return 1;
    if (b.key === UNGROUPED) return -1;
    if (a.firstAt && b.firstAt) return a.firstAt.localeCompare(b.firstAt);
    if (a.firstAt) return -1;
    if (b.firstAt) return 1;
    return a.label.localeCompare(b.label);
  });
}

/** "30 Sep 2026 → 28 Aug 2027", or a single date when a shoot publishes on one day. */
export function shootDateRange(s: Shoot<ShootItem>): string {
  if (!s.firstAt) return "not scheduled";
  const fmt = (iso: string) =>
    new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  const first = fmt(s.firstAt);
  const last = s.lastAt ? fmt(s.lastAt) : first;
  return first === last ? first : `${first} → ${last}`;
}

/** Distinct posts, counting a carousel as the one post it publishes as. */
export function postCount(items: ShootItem[]): number {
  let singles = 0;
  const carousels = new Set<string>();
  for (const it of items) {
    const cid = (it as { carousel_id?: string | null }).carousel_id;
    if (cid) carousels.add(cid);
    else singles += 1;
  }
  return singles + carousels.size;
}
