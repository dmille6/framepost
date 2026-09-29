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
        for (const key of ["post-albums", "post-groups", "post-profiles", "post-performers", "merged-tags"]) {
          void this.qc.invalidateQueries({ queryKey: [key, id] });
        }
      });
      const created = session;
      let status = created.status;
      let error = created.error;
      session.subscribe(() => {
        if (status === created.status && error === created.error) return;
        status = created.status;
        error = created.error;
        if (!created.attached && created.status === "saved" && this.sessions.get(id) === created) {
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
  schedule(id: string, iso: string) {
    return this.scheduling([id], () => schedulePost(id, iso), () => [id]);
  }
  async smartFill(body: SmartFillRequest) {
    if (!body.confirm) {
      await this.prepare(body.post_ids);
      return smartFill(body);
    }
    return this.scheduling(body.post_ids, () => smartFill(body),
      (result) => result.slots.filter((slot) => slot.scheduled_at && !slot.skipped_reason).map((slot) => slot.post_id));
  }
  release(id: string, session: EditorAutosave) {
    void session.flush().then((ok) => {
      if (ok && !session.pending && !session.attached && this.sessions.get(id) === session) {
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
