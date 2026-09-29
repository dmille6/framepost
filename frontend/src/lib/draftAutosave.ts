export type SaveStatus = "saved" | "saving" | "error";

function equal(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** One writer per draft, retained across editor mounts so a late response cannot
 * race a newly opened editor. Server responses never write into the local view. */
export class DraftAutosave<T extends object> {
  readonly view = new Map<string, unknown>();
  attached = false;
  private saved: Partial<T> = {};
  private current: Partial<T> = {};
  private uncertain = new Set<keyof T>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private running: Promise<boolean> | undefined;
  private due = false;
  private cancelled = false;
  private listeners = new Set<() => void>();
  private revision = 0;
  error: string | null = null;
  conflict = false;

  constructor(private save: (patch: Partial<T>, values: Partial<T>) => Promise<void>, private delay = 800) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  snapshot = () => this.revision;
  private notify() {
    this.revision++;
    this.listeners.forEach((listener) => listener());
  }
  get patch(): Partial<T> {
    const patch: Partial<T> = {};
    for (const key of Object.keys(this.current) as (keyof T)[]) {
      if (this.uncertain.has(key) || !equal(this.current[key], this.saved[key])) patch[key] = this.current[key];
    }
    return patch;
  }
  get pending() { return Object.keys(this.patch).length > 0; }
  get status(): SaveStatus {
    return this.error ? "error" : this.pending || this.running ? "saving" : "saved";
  }

  change(name: string, value: unknown, next: Partial<T>, before: Partial<T>) {
    this.view.set(name, value);
    for (const key of Object.keys(next) as (keyof T)[]) {
      if (!(key in this.saved)) this.saved[key] = before[key];
      this.current[key] = next[key];
    }
    clearTimeout(this.timer);
    if (!this.pending && !this.running && !this.conflict) this.error = null;
    if (!this.cancelled) this.timer = setTimeout(() => { this.due = true; void this.saveOnce(); }, this.delay);
    this.notify();
  }

  private saveOnce(): Promise<boolean> {
    if (this.running) return this.running;
    if (this.cancelled || this.conflict) return Promise.resolve(false);
    this.due = false;
    const sent = this.patch;
    const values = { ...this.current };
    if (!Object.keys(sent).length) return Promise.resolve(true);
    this.error = null;
    this.running = Promise.resolve().then(() => this.save(sent, values)).then(() => {
      Object.assign(this.saved, sent);
      for (const key of Object.keys(sent) as (keyof T)[]) this.uncertain.delete(key);
      return true;
    }, (error) => {
      // A multi-endpoint save may have written some fields before failing. Even
      // a reversion to the old baseline must be sent until acknowledged.
      for (const key of Object.keys(sent) as (keyof T)[]) this.uncertain.add(key);
      this.conflict = (error as { status?: number })?.status === 409;
      this.error = error instanceof Error ? error.message : "Could not save this draft.";
      return false;
    }).finally(() => {
      this.running = undefined;
      this.notify();
      if (this.due && !this.error && !this.cancelled) void this.saveOnce();
    });
    this.notify();
    return this.running;
  }

  async flush(): Promise<boolean> {
    clearTimeout(this.timer);
    this.due = false;
    if (this.cancelled || this.conflict) return false;
    // Include edits made while an earlier PATCH is in flight before scheduling.
    while (this.running || this.pending) {
      if (!await (this.running ?? this.saveOnce())) return false;
      clearTimeout(this.timer);
      this.due = false;
      if (this.cancelled || this.conflict) return false;
    }
    return true;
  }

  markConflict(message: string) {
    this.conflict = true;
    this.error = message;
    clearTimeout(this.timer);
    this.notify();
  }

  async cancel() {
    this.cancelled = true;
    clearTimeout(this.timer);
    this.due = false;
    await this.running;
  }

  resume() {
    this.cancelled = false;
    // A rejected delete must leave the photographer's unsaved edits retryable.
    void this.flush();
  }
}
