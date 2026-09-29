import { useEffect, useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import {
  getPost, setPostAlbums, setPostGroups, setPostPerformers, setPostProfiles, updatePost,
  schedulePost, smartFill, type SmartFillRequest,
  type Post, type PostUpdate,
} from "../api/client";
import type { EditorChanges } from "../components/MetadataEditor";
import { DraftAutosave } from "../lib/draftAutosave";

export type EditorAutosave = DraftAutosave<EditorChanges>;

class DraftSaves {
  sessions = new Map<string, EditorAutosave>();
  private listeners = new Set<() => void>();
  private revision = 0;
  private held = new Set<string>();
  private operation: Promise<unknown> = Promise.resolve();
  constructor(private qc: QueryClient) {
    // The store outlives route mounts, including a failed save left on the calendar.
    if (typeof window !== "undefined") {
      window.addEventListener("pagehide", () => { void this.flushAll(); });
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") void this.flushAll();
      });
      window.addEventListener("beforeunload", (event) => {
        if ([...this.sessions.values()].some((session) => session.status !== "saved")) {
          event.preventDefault();
          event.returnValue = "";
        }
      });
    }
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  snapshot = () => this.revision;
  get(id: string) {
    let session = this.sessions.get(id);
    if (!session) {
      session = new DraftAutosave<EditorChanges>(async (changes, values) => {
        const { album_ids, group_ids, use_routing, profile_ids, performer_ids, ...patch } = changes;
        // Relationship endpoints stay explicit and only run for changed selections.
        if (album_ids !== undefined) await setPostAlbums(id, album_ids, { autosave: true });
        if (group_ids !== undefined || use_routing !== undefined) {
          await setPostGroups(id, group_ids ?? values.group_ids ?? [], use_routing ?? values.use_routing ?? false, { autosave: true });
        }
        if (profile_ids !== undefined) await setPostProfiles(id, profile_ids, { autosave: true });
        if (performer_ids !== undefined) await setPostPerformers(id, performer_ids, { autosave: true });
        // Readiness must include the relationship writes above as well as metadata.
        const saved = Object.keys(patch).length
          ? await updatePost(id, patch as PostUpdate, { autosave: true })
          : await getPost(id);
        if (saved.status !== "pending" || saved.scheduled_at != null) {
          throw Object.assign(new Error("This post was scheduled or is no longer a draft. Discard these edits and open it from the calendar to edit."), { status: 409 });
        }
        const listFetching = this.qc.isFetching({ queryKey: ["drafts"] }) > 0;
        this.qc.setQueryData<Post[]>(["drafts"], (old) =>
          (old ?? []).map((post) => post.id === id ? saved : post),
        );
        this.qc.setQueryData(["post", id], saved);
        // Restart an outstanding list refresh instead of cancelling and losing it.
        if (listFetching) void this.qc.invalidateQueries({ queryKey: ["drafts"] });
        const relationships = [
          ["post-albums", album_ids !== undefined],
          ["post-groups", group_ids !== undefined || use_routing !== undefined],
          ["post-profiles", profile_ids !== undefined],
          ["post-performers", performer_ids !== undefined],
        ] as const;
        for (const [key, changed] of relationships) {
          if (changed) void this.qc.invalidateQueries({ queryKey: [key, id] });
        }
        if (patch.tags !== undefined || profile_ids !== undefined) {
          void this.qc.invalidateQueries({ queryKey: ["merged-tags", id] });
        }
      });
      const created = session;
      let status = created.status;
      let error = created.error;
      session.subscribe(() => {
        if (status === created.status && error === created.error) return;
        status = created.status;
        error = created.error;
        if (!this.held.has(id) && !created.attached && created.status === "saved" && this.sessions.get(id) === created) {
          this.sessions.delete(id);
        }
        this.revision++;
        this.listeners.forEach((listener) => listener());
      });
      this.sessions.set(id, session);
    }
    return session;
  }
  async flushAll() {
    return (await Promise.all([...this.sessions.values()].map((session) => session.flush()))).every(Boolean);
  }
  async prepare(ids: string[]) {
    const results = await Promise.all(ids.map((id) => this.sessions.get(id)?.flush() ?? true));
    if (results.some((ok) => !ok)) {
      const error = ids.map((id) => this.sessions.get(id)?.error).find(Boolean);
      throw new Error(error || "Couldn't save. Retry before scheduling.");
    }
  }
  remove(ids: string[], retainPending = false) {
    for (const id of ids) {
      const session = this.sessions.get(id);
      void session?.cancel();
      if (retainPending && session?.pending) {
        session.markConflict(`${Object.keys(session.patch).length} unsaved field(s) on a now-scheduled post. Open it from the calendar to apply these edits, or discard them.`);
      } else {
        this.sessions.delete(id);
      }
    }
    this.qc.setQueryData<Post[]>(["drafts"], (old) => old?.filter((post) => !ids.includes(post.id)));
    // Replace any list request whose snapshot predates scheduling/deletion.
    void this.qc.invalidateQueries({ queryKey: ["drafts"] });
  }
  async discard(id: string) {
    const session = this.sessions.get(id);
    if (!session?.conflict) return;
    await session.cancel();
    this.sessions.delete(id);
    this.revision++;
    this.listeners.forEach((listener) => listener());
    this.qc.setQueryData<Post[]>(["drafts"], (old) => old?.filter((post) => post.id !== id));
    void this.qc.invalidateQueries({ queryKey: ["drafts"] });
    void this.qc.invalidateQueries({ queryKey: ["post", id] });
  }
  private async scheduling<R>(ids: string[], action: () => Promise<R>, completed: (result: R) => string[]) {
    await this.prepare(ids);
    const sessions = ids.map((id) => this.sessions.get(id)).filter((s) => s !== undefined);
    await Promise.all(sessions.map((session) => session.cancel()));
    try {
      const result = await action();
      const removed = completed(result);
      this.remove(removed, true);
      for (const id of ids) if (!removed.includes(id)) this.sessions.get(id)?.resume();
      return result;
    } catch (error) {
      for (const session of sessions) session.resume();
      throw error;
    }
  }
  private exclusive<R>(action: () => Promise<R>): Promise<R> {
    const result = this.operation.then(action);
    this.operation = result.catch(() => {});
    return result;
  }
  saveCrops(edits: { post: Post; patch: PostUpdate }[]) {
    return this.exclusive(async () => {
      const ids = edits.map(({ post }) => post.id);
      ids.forEach((id) => this.held.add(id));
      const sessions = ids.map((id) => this.get(id));
      try {
        await this.prepare(ids);
        await Promise.all(sessions.map((session) => session.cancel()));
        for (const { post, patch } of edits) {
          const latest = await getPost(post.id);
          if (latest.status !== "pending" || latest.scheduled_at != null) {
            throw new Error("This post is no longer a draft. Reopen it from the calendar to edit.");
          }
          // Never replay an opening snapshot over an acknowledged edit. A retry
          // may encounter values already saved by an earlier part of this batch.
          const remaining: PostUpdate = { ...patch };
          for (const key of Object.keys(patch) as (keyof PostUpdate)[]) {
            const current = latest[key as keyof Post] ?? null;
            if (current === patch[key]) delete remaining[key];
            else if (current !== (post[key as keyof Post] ?? null)) {
              throw new Error("A crop changed since this filmstrip opened. Close and reopen it to use the latest edits.");
            }
          }
          if (!Object.keys(remaining).length) continue;
          const saved = await updatePost(post.id, remaining, { draft_only: true });
          this.qc.setQueryData<Post[]>(["drafts"], (old) => old?.map((p) => p.id === saved.id ? saved : p));
          this.qc.setQueryData(["post", post.id], saved);
          // Replace any response whose snapshot predates this explicit crop save.
          if (this.qc.isFetching({ queryKey: ["drafts"] })) void this.qc.invalidateQueries({ queryKey: ["drafts"] });
          if (this.qc.isFetching({ queryKey: ["post", post.id] })) void this.qc.invalidateQueries({ queryKey: ["post", post.id] });
        }
      } finally {
        for (const id of ids) this.held.delete(id);
        for (const [index, session] of sessions.entries()) {
          session.resume();
          if (!session.attached && !session.pending && !session.error) this.sessions.delete(ids[index]);
        }
      }
    });
  }
  schedule(id: string, iso: string) {
    return this.exclusive(() => this.scheduling([id], () => schedulePost(id, iso), () => [id]));
  }
  async smartFill(body: SmartFillRequest) {
    if (!body.confirm) {
      await this.prepare(body.post_ids);
      return smartFill(body);
    }
    return this.exclusive(() => this.scheduling(body.post_ids, () => smartFill(body),
      (result) => result.slots.filter((slot) => slot.scheduled_at && !slot.skipped_reason).map((slot) => slot.post_id)));
  }
  release(id: string, session: EditorAutosave) {
    void session.flush().then((ok) => {
      if (!this.held.has(id) && ok && !session.pending && !session.attached && this.sessions.get(id) === session) {
        this.sessions.delete(id);
      }
    });
  }
}

// Survives route navigation as well as card switches; failed saves remain retryable.
const stores = new WeakMap<QueryClient, DraftSaves>();
export function useDraftAutosaves(qc: QueryClient) {
  let store = stores.get(qc);
  if (!store) { store = new DraftSaves(qc); stores.set(qc, store); }
  useSyncExternalStore(store.subscribe, store.snapshot);
  useEffect(() => () => { void store.flushAll(); }, [store]);
  return store;
}
