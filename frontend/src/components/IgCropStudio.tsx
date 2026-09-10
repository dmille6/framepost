import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchCropAnchor, igPreviewUrl, previewUrl } from "../api/client";

export type IgFit = "crop" | "pad" | "pad_blur";
export type CropRect = { x: number; y: number; w: number; h: number };
/** Photographer-set anchor, normalized 0..1 in source coordinates. */
export type FocalPoint = { x: number; y: number };

/** Instagram's landscape ceiling — not in dispute, mirrors ig_variant.MAX_ASPECT. */
const MAX_ASPECT = 1.91;
const RATIOS: Record<string, number> = { "3:4": 3 / 4, "4:5": 4 / 5 };

/** Mirrors ig_variant.FACE_ANCHOR — where the anchor sits inside the window. */
const FACE_ANCHOR = 0.38;

/**
 * The window auto would cut around `anchor`. This duplicates ig_variant.auto_window on
 * purpose: the server stays authoritative for what actually publishes, but dragging the
 * marker has to re-anchor the box at pointer speed, and a round-trip per frame isn't
 * that. test_ig_focal_point.py pins the same numbers on the Python side — if these two
 * drift, that test and this one fail together rather than the preview quietly lying.
 */
export function autoWindowFor(
  anchor: { x: number; y: number },
  srcRatio: number,
  target: number,
): CropRect {
  const place = (along: number, frac: number) => {
    const movable = 1 - frac;
    if (movable <= 0) return 0.5;
    return Math.min(1, Math.max(0, (along - FACE_ANCHOR * frac) / movable));
  };
  if (srcRatio < target) {
    const frac = Math.min(1, srcRatio / target);
    return { x: 0, y: place(anchor.y, frac) * (1 - frac), w: 1, h: frac };
  }
  if (srcRatio > target) {
    const frac = Math.min(1, target / srcRatio);
    return { x: place(anchor.x, frac) * (1 - frac), y: 0, w: frac, h: 1 };
  }
  return { x: 0, y: 0, w: 1, h: 1 };
}

/**
 * Direct-manipulation crop for the Instagram variant. Flickr always gets the full
 * frame; only Instagram needs a window.
 *
 * The canvas is a PREVIEW, never the source of truth: it emits a normalized rect and
 * the server crops exactly that rect with render_variant(). Anything else lets the
 * preview and the published post drift apart, which is worse than showing no preview.
 */
export default function IgCropStudio({
  postId,
  width,
  height,
  ratioKey,
  fit,
  rect,
  offset,
  focal,
  onFitChange,
  onRectChange,
  onFocalChange,
}: {
  postId: string;
  width: number | null;
  height: number | null;
  /** Learned floor from app_config (ig_min_ratio_support). */
  ratioKey: string;
  fit: IgFit;
  rect: CropRect | null;
  offset: number | null;
  /** Unsaved focal-point edit. null = whatever the server last stored. */
  focal: FocalPoint | null;
  onFitChange: (f: IgFit) => void;
  /** null clears the rect and hands the window back to anchored auto. */
  onRectChange: (r: CropRect | null) => void;
  /** null hands the anchor back to face detection. */
  onFocalChange: (f: FocalPoint | null) => void;
}) {
  const nw = width || 0;
  const nh = height || 0;
  const srcRatio = nw && nh ? nw / nh : 1;

  // Same rule the worker applies: panoramas cap at 1.91, everything else uses the floor.
  const target =
    srcRatio > MAX_ASPECT ? MAX_ASPECT : RATIOS[ratioKey] ?? RATIOS["4:5"];
  const targetLabel = srcRatio > MAX_ASPECT ? "1.91:1" : ratioKey;

  const boxRef = useRef<HTMLDivElement | null>(null);
  const [stage, setStage] = useState({ w: 300, h: 375 });
  const [drag, setDrag] = useState(false);

  // Window geometry, sized to fit the available column.
  useEffect(() => {
    const measure = () => {
      const avail = Math.min(340, (boxRef.current?.clientWidth ?? 340) - 4);
      let h = Math.min(380, avail / target);
      let w = h * target;
      if (w > avail) {
        w = avail;
        h = w / target;
      }
      setStage({ w, h });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [target]);

  // Where the auto-crop anchors, resolved server-side against the source image — the
  // same point and the same window the worker would use. Showing it explains why "auto"
  // put the window where it did, and warns when a manual crop has cut the subject out.
  // Always asks for the detection answer: this component owns the focal point (the
  // parent hands it in and saves it), so the server's copy would only go stale mid-edit.
  const { data: anchor } = useQuery({
    queryKey: ["crop-anchor", postId, "no-focal"],
    queryFn: () => fetchCropAnchor(postId, true),
    enabled: fit === "crop",
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  // The marker follows the unsaved edit while dragging, and the stored answer otherwise.
  const anchorX = focal?.x ?? anchor?.anchor_x ?? null;
  const anchorY = focal?.y ?? anchor?.anchor_y ?? null;
  const anchorSource: "focal" | "face" | "center" | null =
    focal ? "focal" : anchor?.source ?? null;

  // A rect of the full frame refitted to the target — what "auto" looks like spatially,
  // and the starting point when the photographer first grabs the image.
  //
  // The server hands back the window auto would actually cut, anchor and all, so the box
  // on screen is the box the worker cuts. Before that request lands (and whenever a
  // legacy single-axis offset is stored, which still outranks auto) this falls back to
  // the offset math below.
  const maxAreaRect = useMemo<CropRect>(() => {
    if (!nw || !nh) return { x: 0, y: 0, w: 1, h: 1 };
    if (offset === null && focal) return autoWindowFor(focal, srcRatio, target);
    if (offset === null && anchor) return anchor.auto;
    if (srcRatio < target) {
      const h = srcRatio / target;
      return { x: 0, y: (offset ?? 0.5) * (1 - h), w: 1, h };
    }
    if (srcRatio > target) {
      const w = target / srcRatio;
      return { x: (offset ?? 0.5) * (1 - w), y: 0, w, h: 1 };
    }
    return { x: 0, y: 0, w: 1, h: 1 };
  }, [nw, nh, srcRatio, target, offset, anchor, focal]);

  // Held still while the anchor is being dragged. Auto re-anchors the window around the
  // focal point, so without this the photo slides under the cursor as you drag, the
  // point you're aiming at keeps changing, and the marker chases the pointer. Freeze on
  // pointer-down, re-anchor on release.
  const [frozenWindow, setFrozenWindow] = useState<CropRect | null>(null);
  const active = rect ?? frozenWindow ?? maxAreaRect;

  // Zoom is derived, not stored: 1 = the maximum-area window, higher = tighter.
  const zoom = useMemo(() => {
    const base = Math.max(maxAreaRect.w, maxAreaRect.h);
    const cur = Math.max(active.w, active.h);
    return cur > 0 ? base / cur : 1;
  }, [active, maxAreaRect]);

  const clampRect = useCallback((r: CropRect): CropRect => {
    const w = Math.min(1, Math.max(0.05, r.w));
    const h = Math.min(1, Math.max(0.05, r.h));
    return {
      w,
      h,
      x: Math.min(Math.max(0, r.x), 1 - w),
      y: Math.min(Math.max(0, r.y), 1 - h),
    };
  }, []);

  const setZoom = useCallback(
    (z: number) => {
      const zz = Math.min(4, Math.max(1, z));
      const w = maxAreaRect.w / zz;
      const h = maxAreaRect.h / zz;
      const cx = active.x + active.w / 2;
      const cy = active.y + active.h / 2;
      onRectChange(clampRect({ x: cx - w / 2, y: cy - h / 2, w, h }));
    },
    [active, maxAreaRect, clampRect, onRectChange],
  );

  // --- dragging: pixels on screen -> fractions of the source ---
  const last = useRef<{ x: number; y: number } | null>(null);
  const onDown = (e: React.PointerEvent) => {
    if (fit !== "crop") return;
    setDrag(true);
    last.current = { x: e.clientX, y: e.clientY };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!drag || !last.current) return;
    const dxPx = e.clientX - last.current.x;
    const dyPx = e.clientY - last.current.y;
    last.current = { x: e.clientX, y: e.clientY };
    // The window shows `active.w` of the source across `stage.w` pixels, so dragging
    // the photo right by N px moves the window left by N * (active.w / stage.w).
    onRectChange(
      clampRect({
        ...active,
        x: active.x - (dxPx * active.w) / stage.w,
        y: active.y - (dyPx * active.h) / stage.h,
      }),
    );
  };
  const endDrag = () => {
    setDrag(false);
    last.current = null;
  };

  const onWheel = (e: React.WheelEvent) => {
    if (fit !== "crop") return;
    e.preventDefault();
    setZoom(zoom * (e.deltaY < 0 ? 1.08 : 0.92));
  };

  // Displayed image size so `active` exactly fills the window.
  const dispW = stage.w / active.w;
  const dispH = stage.h / active.h;
  const offX = -active.x * dispW;
  const offY = -active.y * dispH;

  const isAuto = rect === null;
  const kept = Math.round(active.w * active.h * 100);

  // Source coords -> stage pixels, using the same transform as the image itself.
  const faceMarker = useMemo(() => {
    if (fit !== "crop" || anchorX == null || anchorY == null) return null;
    const rawLeft = offX + anchorX * dispW;
    const rawTop = offY + anchorY * dispH;
    const inside =
      rawLeft >= 0 && rawTop >= 0 && rawLeft <= stage.w && rawTop <= stage.h;
    // When the face sits outside the window, pin the marker to the nearest edge rather
    // than hiding it. Losing the face is precisely the moment you want to be told, and
    // an edge-pinned marker also shows which way to drag to get it back.
    const pad = 15;
    return {
      left: Math.min(Math.max(pad, rawLeft), stage.w - pad),
      top: Math.min(Math.max(pad, rawTop), stage.h - pad),
      inside,
    };
  }, [fit, anchorX, anchorY, offX, offY, dispW, dispH, stage.w, stage.h]);

  // --- dragging the anchor itself ---
  // Stage pixels back to source fractions — the exact inverse of the transform above,
  // so the point lands where the cursor is rather than drifting under zoom.
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [markerDrag, setMarkerDrag] = useState(false);

  const focalFromClient = useCallback(
    (clientX: number, clientY: number): FocalPoint | null => {
      const box = stageRef.current?.getBoundingClientRect();
      if (!box || !dispW || !dispH) return null;
      return {
        x: Math.min(1, Math.max(0, (clientX - box.left - offX) / dispW)),
        y: Math.min(1, Math.max(0, (clientY - box.top - offY) / dispH)),
      };
    },
    [offX, offY, dispW, dispH],
  );

  const onMarkerDown = (e: React.PointerEvent) => {
    if (fit !== "crop") return;
    // Without this the photo pans underneath and the marker never moves relative to it.
    e.stopPropagation();
    e.preventDefault();
    setMarkerDrag(true);
    setFrozenWindow(rect ?? maxAreaRect);
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };
  const onMarkerMove = (e: React.PointerEvent) => {
    if (!markerDrag) return;
    e.stopPropagation();
    const next = focalFromClient(e.clientX, e.clientY);
    if (next) onFocalChange(next);
  };
  const onMarkerUp = (e: React.PointerEvent) => {
    if (!markerDrag) return;
    e.stopPropagation();
    setMarkerDrag(false);
    setFrozenWindow(null);
  };
  // Arrow keys for the last few pixels — a 30px circle is not a precision instrument.
  const onMarkerKey = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 0.02 : 0.005;
    const d: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0], ArrowRight: [step, 0],
      ArrowUp: [0, -step], ArrowDown: [0, step],
    };
    const move = d[e.key];
    if (!move || anchorX == null || anchorY == null) return;
    e.preventDefault();
    onFocalChange({
      x: Math.min(1, Math.max(0, anchorX + move[0])),
      y: Math.min(1, Math.max(0, anchorY + move[1])),
    });
  };

  return (
    <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
      <div ref={boxRef} style={{ flex: "0 0 auto" }}>
        {fit === "crop" ? (
          <div
            ref={stageRef}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onWheel={onWheel}
            role="application"
            aria-label="Instagram crop window — drag to reposition, scroll to zoom"
            style={{
              width: stage.w,
              height: stage.h,
              position: "relative",
              overflow: "hidden",
              background: "#000",
              borderRadius: 8,
              border: "0.5px solid var(--border-strong)",
              cursor: drag ? "grabbing" : "grab",
              touchAction: "none",
              userSelect: "none",
            }}
          >
            <img
              src={previewUrl(postId)}
              alt=""
              draggable={false}
              style={{
                position: "absolute",
                width: dispW,
                height: dispH,
                transform: `translate(${offX}px, ${offY}px)`,
                transformOrigin: "0 0",
                display: "block",
              }}
            />
            {/* Thirds, drawn only while positioning so they don't clutter at rest. */}
            {drag && (
              <>
                {[33.33, 66.66].map((p) => (
                  <div key={`v${p}`} style={{ position: "absolute", top: 0, bottom: 0,
                    left: `${p}%`, width: 1, background: "rgba(255,255,255,.45)" }} />
                ))}
                {[33.33, 66.66].map((p) => (
                  <div key={`h${p}`} style={{ position: "absolute", left: 0, right: 0,
                    top: `${p}%`, height: 1, background: "rgba(255,255,255,.45)" }} />
                ))}
              </>
            )}
            {faceMarker && (
              <div
                role="slider"
                tabIndex={0}
                aria-label="Crop focal point"
                aria-valuetext={`${Math.round((anchorX ?? 0) * 100)}% across, ${Math.round(
                  (anchorY ?? 0) * 100,
                )}% down`}
                aria-valuenow={Math.round((anchorX ?? 0) * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
                onPointerDown={onMarkerDown}
                onPointerMove={onMarkerMove}
                onPointerUp={onMarkerUp}
                onPointerCancel={onMarkerUp}
                onKeyDown={onMarkerKey}
                title={
                  !faceMarker.inside
                    ? "The focal point is outside this crop — drag the photo toward this edge"
                    : anchorSource === "focal"
                      ? "Your focal point — what the auto crop anchors on. Drag to move it."
                      : anchorSource === "face"
                        ? "Detected face — what the auto crop anchors on. Drag to put it somewhere else."
                        : "No face found, so auto centres. Drag this to say what matters."
                }
                style={{
                  position: "absolute",
                  left: faceMarker.left,
                  top: faceMarker.top,
                  width: 30,
                  height: 30,
                  marginLeft: -15,
                  marginTop: -15,
                  borderRadius: "50%",
                  border: `1.5px solid ${faceMarker.inside ? "rgba(93,202,165,0.95)" : "var(--danger)"}`,
                  boxShadow: "0 0 0 1px rgba(0,0,0,0.45)",
                  cursor: markerDrag ? "grabbing" : "grab",
                  touchAction: "none",
                  display: "grid",
                  placeItems: "center",
                }}
              >
                {/* A filled centre distinguishes "I chose this" from "a detector guessed
                    it" — otherwise the two states look identical and you can't tell
                    whether your drag actually took. */}
                {anchorSource === "focal" && (
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: faceMarker.inside
                        ? "rgba(93,202,165,0.95)"
                        : "var(--danger)",
                      boxShadow: "0 0 0 1px rgba(0,0,0,0.45)",
                    }}
                  />
                )}
              </div>
            )}
          </div>
        ) : (
          // pad / pad_blur have no window to position — show what the server will make.
          <img
            key={fit}
            src={igPreviewUrl(postId, fit, null)}
            alt="Instagram preview"
            style={{
              width: stage.w,
              borderRadius: 8,
              border: "0.5px solid var(--border-strong)",
              background: "var(--surface)",
              display: "block",
            }}
          />
        )}
      </div>

      <div style={{ flex: 1, minWidth: 220, display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {([["crop", "Smart crop"], ["pad", "Pad black"], ["pad_blur", "Pad blur"]] as const)
            .map(([value, label]) => {
              const on = fit === value;
              return (
                <button
                  key={value}
                  type="button"
                  onClick={() => onFitChange(value)}
                  style={{
                    padding: "6px 12px",
                    border: `0.5px solid ${on ? "rgba(93,202,165,0.3)" : "var(--border-strong)"}`,
                    background: on ? "var(--teal-tint)" : "transparent",
                    color: on ? "var(--text)" : "var(--text-dim)",
                    borderRadius: 999,
                    fontSize: 12,
                    fontWeight: on ? 500 : 400,
                    cursor: "pointer",
                  }}
                >
                  {label}
                </button>
              );
            })}
        </div>

        {fit === "crop" && (
          <>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label
                htmlFor="ig-zoom"
                style={{ fontSize: 11, color: "var(--text-dim)", display: "flex",
                         justifyContent: "space-between" }}
              >
                <span>Zoom</span>
                <span>{zoom.toFixed(2)}×</span>
              </label>
              <input
                id="ig-zoom"
                type="range"
                min={1}
                max={4}
                step={0.01}
                value={zoom}
                onChange={(e) => setZoom(Number(e.target.value))}
                style={{ accentColor: "var(--teal)", width: "100%" }}
              />
            </div>

            <div style={{ fontSize: 11, color: "var(--text-dim)", lineHeight: 1.6 }}>
              Target <strong style={{ color: "var(--text)" }}>{targetLabel}</strong> ·
              keeping <strong style={{ color: "var(--text)" }}>{kept}%</strong> of the frame
              <br />
              {isAuto ? (
                <span style={{ color: "var(--teal)" }}>
                  {anchorSource === "focal"
                    ? "Auto — your focal point"
                    : anchorSource === "center"
                      ? "Auto — centred (no face found)"
                      : "Auto — face-anchored"}
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => onRectChange(null)}
                  style={{
                    background: "none", border: "none", padding: 0,
                    color: "var(--teal)", cursor: "pointer", fontSize: 11,
                    textDecoration: "underline", textUnderlineOffset: 2,
                  }}
                >
                  Reset to auto
                </button>
              )}
            </div>

            {faceMarker && !faceMarker.inside && (
              <div style={{ fontSize: 11, color: "var(--danger)", lineHeight: 1.5 }}>
                The focal point falls outside this crop.
              </div>
            )}

            {anchorSource === "focal" && (
              <button
                type="button"
                onClick={() => onFocalChange(null)}
                style={{
                  background: "none", border: "none", padding: 0, textAlign: "left",
                  color: "var(--teal)", cursor: "pointer", fontSize: 11,
                  textDecoration: "underline", textUnderlineOffset: 2,
                }}
              >
                Reset to detected face
              </button>
            )}

            <div style={{ fontSize: 10.5, color: "var(--text-fade)", lineHeight: 1.5 }}>
              Drag the photo to reposition · scroll to zoom.<br />
              Drag the circle to say what the photo is about — auto crops around it.<br />
              Flickr still receives the full frame.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
