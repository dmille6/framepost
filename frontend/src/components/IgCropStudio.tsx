import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchFaceCenter, igPreviewUrl, previewUrl } from "../api/client";

export type IgFit = "crop" | "pad" | "pad_blur";
export type CropRect = { x: number; y: number; w: number; h: number };

/** Instagram's landscape ceiling — not in dispute, mirrors ig_variant.MAX_ASPECT. */
const MAX_ASPECT = 1.91;
const RATIOS: Record<string, number> = { "3:4": 3 / 4, "4:5": 4 / 5 };

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
  onFitChange,
  onRectChange,
}: {
  postId: string;
  width: number | null;
  height: number | null;
  /** Learned floor from app_config (ig_min_ratio_support). */
  ratioKey: string;
  fit: IgFit;
  rect: CropRect | null;
  offset: number | null;
  onFitChange: (f: IgFit) => void;
  /** null clears the rect and hands the window back to face-anchored auto. */
  onRectChange: (r: CropRect | null) => void;
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

  // A rect of the full frame refitted to the target — what "auto" looks like spatially,
  // and the starting point when the photographer first grabs the image.
  const maxAreaRect = useMemo<CropRect>(() => {
    if (!nw || !nh) return { x: 0, y: 0, w: 1, h: 1 };
    if (srcRatio < target) {
      const h = srcRatio / target;
      return { x: 0, y: (offset ?? 0.5) * (1 - h), w: 1, h };
    }
    if (srcRatio > target) {
      const w = target / srcRatio;
      return { x: (offset ?? 0.5) * (1 - w), y: 0, w, h: 1 };
    }
    return { x: 0, y: 0, w: 1, h: 1 };
  }, [nw, nh, srcRatio, target, offset]);

  const active = rect ?? maxAreaRect;

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

  // Where the auto-crop anchors. Detection runs server-side on the source image, so
  // this is the same point the worker would centre on — showing it explains why "auto"
  // put the window where it did, and warns when a manual crop has cut the face out.
  const { data: face } = useQuery({
    queryKey: ["face-center", postId],
    queryFn: () => fetchFaceCenter(postId),
    enabled: fit === "crop",
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  // Displayed image size so `active` exactly fills the window.
  const dispW = stage.w / active.w;
  const dispH = stage.h / active.h;
  const offX = -active.x * dispW;
  const offY = -active.y * dispH;

  const isAuto = rect === null;
  const kept = Math.round(active.w * active.h * 100);

  // Source coords -> stage pixels, using the same transform as the image itself.
  const faceMarker = useMemo(() => {
    if (fit !== "crop" || !face?.detected || face.x == null || face.y == null) return null;
    const rawLeft = offX + face.x * dispW;
    const rawTop = offY + face.y * dispH;
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
  }, [fit, face, offX, offY, dispW, dispH, stage.w, stage.h]);

  return (
    <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
      <div ref={boxRef} style={{ flex: "0 0 auto" }}>
        {fit === "crop" ? (
          <div
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
                aria-hidden="true"
                title={
                  faceMarker.inside
                    ? "Detected face — what the auto crop anchors on"
                    : "The detected face is outside this crop — drag toward this edge"
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
                  pointerEvents: "none",
                }}
              />
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
                <span style={{ color: "var(--teal)" }}>Auto — face-anchored</span>
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
                The detected face falls outside this crop.
              </div>
            )}

            <div style={{ fontSize: 10.5, color: "var(--text-fade)", lineHeight: 1.5 }}>
              Drag the photo to reposition · scroll to zoom.<br />
              Flickr still receives the full frame.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
