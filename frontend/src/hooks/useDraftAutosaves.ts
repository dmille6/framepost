import { useEffect, useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import {
  getPost, setPostAlbums, setPostGroups, setPostPerformers, setPostProfiles, updatePost,
  type Post, type PostUpdate,
} from "../api/client";
import type { EditorChanges } from "../components/MetadataEditor";
import { DraftAutosave } from "../lib/draftAutosave";

export type EditorAutosave = DraftAutosave<EditorChanges>;

class DraftSaves {
  sessions = new Map<string, EditorAutosave>();
  private listeners = new Set<() => void>();
  private revision = 0;
  constructor(private qc: QueryClient) {}
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
        if (album_ids !== undefined) await setPostAlbums(id, album_ids);
        if (group_ids !== undefined || use_routing !== undefined) {
          await setPostGroups(id, group_ids ?? values.group_ids ?? [], use_routing ?? values.use_routing ?? false);
        }
        if (profile_ids !== undefined) await setPostProfiles(id, profile_ids);
        if (performer_ids !== undefined) await setPostPerformers(id, performer_ids);
        // Readiness must include the relationship writes above as well as metadata.
        const saved = Object.keys(patch).length
          ? await updatePost(id, patch as PostUpdate, { autosave: true })
          : await getPost(id);
        await this.qc.cancelQueries({ queryKey: ["drafts"] });
        this.qc.setQueryData<Post[]>(["drafts"], (old) =>
          (old ?? []).map((post) => post.id === id ? saved : post),
        );
        this.qc.setQueryData(["post", id], saved);
        for (const key of ["post-albums", "post-groups", "post-profiles", "post-performers", "merged-tags"]) {
          void this.qc.invalidateQueries({ queryKey: [key, id] });
        }
      });
      const created = session;
      session.subscribe(() => {
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
  flushAll() { for (const session of this.sessions.values()) void session.flush(); }
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
  useEffect(() => () => store.flushAll(), [store]);
  return store;
}
