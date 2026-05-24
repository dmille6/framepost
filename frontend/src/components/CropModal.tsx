// Per-photo crop selector for the Reels builder. Locked 9:16 aspect.
//
// Two modes:
//   - Simple: one crop. The Reel generator pans/zooms gently within it (auto Ken Burns).
//   - Director: pick a Start crop and an End crop. The Reel generator interpolates
//     between them frame-by-frame, so the camera "pushes in" or "pulls back" between
//     your chosen positions. Worth the extra work for hero shots.
//
// react-easy-crop reports coords in pixels of the displayed image. We display the
// 1600-px preview (originals can be 60MP) and scale crop coordinates back up to the
// original-image space for the backend renderer.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Cropper, { type Area } from "react-easy-crop";

import { fetchFaceCenter, previewUrl, type ReelCrop } from "../api/client";

const ASPECT = 9 / 16;
const PREVIEW_LONG_EDGE = 1600;

function previewDims(srcW: number, srcH: number): { w: number; h: number } {
  const longEdge = Math.max(srcW, srcH);
  if (longEdge <= PREVIEW_LONG_EDGE) return { w: srcW, h: srcH };
  const scale = PREVIEW_LONG_EDGE / longEdge;
  return { w: Math.round(srcW * scale), h: Math.round(srcH * scale) };
}

function scaleCropToOriginal(
  cropInPreviewPx: Area,
  srcW: number,
  srcH: number,
): ReelCrop {
  const { w: pw, h: ph } = previewDims(srcW, srcH);
  const sx = srcW / pw;
  const sy = srcH / ph;
  return {
    x: Math.max(0, Math.round(cropInPreviewPx.x * sx)),
    y: Math.max(0, Math.round(cropInPreviewPx.y * sy)),
    width: Math.min(srcW, Math.round(cropInPreviewPx.width * sx)),
    height: Math.min(srcH, Math.round(cropInPreviewPx.height * sy)),
  };
}

function originalCropToPreviewArea(
  c: ReelCrop,
  srcW: number,
  srcH: number,
): Area {
  const { w: pw, h: ph } = previewDims(srcW, srcH);
  const sx = pw / srcW;
  const sy = ph / srcH;
  return {
    x: c.x * sx,
    y: c.y * sy,
    width: c.width * sx,
    height: c.height * sy,
  };
}

// Compute a 9:16 crop area (in preview-pixel space, ready for react-easy-crop's
// initialCroppedAreaPixels prop) centered on a normalized (xFrac, yFrac) point.
// Falls back to image center if the point is undefined.
function previewAreaCenteredOn(
  xFrac: number | null | undefined,
  yFrac: number | null | undefined,
  srcW: number,
  srcH: number,
): Area {
  const { w: pw, h: ph } = previewDims(srcW, srcH);
  const srcAspect = pw / ph;
  let cropW: number;
  let cropH: number;
  if (srcAspect > ASPECT) {
    cropH = ph;
    cropW = Math.round(ph * ASPECT);
  } else {
    cropW = pw;
    cropH = Math.round(pw / ASPECT);
  }
  const cx = (xFrac ?? 0.5) * pw;
  const cy = (yFrac ?? 0.5) * ph;
  let x = Math.round(cx - cropW / 2);
  let y = Math.round(cy - cropH / 2);
  x = Math.max(0, Math.min(pw - cropW, x));
  y = Math.max(0, Math.min(ph - cropH, y));
  return { x, y, width: cropW, height: cropH };
}

type Mode = "simple" | "director";
type Tab = "start" | "end";

type Props = {
  postId: string;
  postWidth: number;
  postHeight: number;
  initialCrop: ReelCrop | null;
  initialCropEnd?: ReelCrop | null;
  /** When called with end=null, simple mode. Else director mode. */
  onSave: (start: ReelCrop, end: ReelCrop | null) => void;
  onCancel: () => void;
};

export default function CropModal({
  postId,
  postWidth,
  postHeight,
  initialCrop,
  initialCropEnd,
  onSave,
  onCancel,
}: Props) {
  const [mode, setMode] = useState<Mode>(initialCropEnd ? "director" : "simple");
  const [tab, setTab] = useState<Tab>("start");

  // Two parallel react-easy-crop states. Each tab has its own crop+zoom+pixel rect.
  const [startCrop, setStartCrop] = useState({ x: 0, y: 0 });
  const [startZoom, setStartZoom] = useState(1);
  const [startPixel, setStartPixel] = useState<Area | null>(null);

  const [endCrop, setEndCrop] = useState({ x: 0, y: 0 });
  const [endZoom, setEndZoom] = useState(1);
  const [endPixel, setEndPixel] = useState<Area | null>(null);

  // Face-detection hint for the initial Start position when there's no saved crop yet.
  // (End defaults to the same place — you drag from there to where the camera should
  // land.)
  const faceQuery = useQuery({
    queryKey: ["face-center", postId],
    queryFn: () => fetchFaceCenter(postId),
    enabled: !initialCrop,
    staleTime: 60_000,
  });

  const startInitialArea = useMemo(() => {
    if (initialCrop) {
      return originalCropToPreviewArea(initialCrop, postWidth, postHeight);
    }
    if (!faceQuery.data) return undefined;
    return previewAreaCenteredOn(
      faceQuery.data.detected ? faceQuery.data.x : undefined,
      faceQuery.data.detected ? faceQuery.data.y : undefined,
      postWidth,
      postHeight,
    );
  }, [initialCrop, faceQuery.data, postWidth, postHeight]);

  const endInitialArea = useMemo(() => {
    if (initialCropEnd) {
      return originalCropToPreviewArea(initialCropEnd, postWidth, postHeight);
    }
    // Default end position = same as start, user will drag.
    return startInitialArea;
  }, [initialCropEnd, startInitialArea, postWidth, postHeight]);

  const isStart = tab === "start";
  const currentCrop = isStart ? startCrop : endCrop;
  const currentZoom = isStart ? startZoom : endZoom;
  const currentInitialArea = isStart ? startInitialArea : endInitialArea;

  const onCropComplete = useCallback(
    (_area: Area, areaPixels: Area) => {
      if (isStart) setStartPixel(areaPixels);
      else setEndPixel(areaPixels);
    },
    [isStart],
  );

  function handleSave() {
    if (!startPixel) return;
    const startScaled = scaleCropToOriginal(startPixel, postWidth, postHeight);
    if (mode === "simple") {
      onSave(startScaled, null);
      return;
    }
    if (!endPixel) return;
    const endScaled = scaleCropToOriginal(endPixel, postWidth, postHeight);
    onSave(startScaled, endScaled);
  }

  // Arrow-key 1px nudges on the active tab.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onCancel();
        return;
      }
      if (e.key === "Enter") {
        handleSave();
        return;
      }
      const nudge: Record<string, [number, number]> = {
        ArrowLeft: [1, 0],
        ArrowRight: [-1, 0],
        ArrowUp: [0, 1],
        ArrowDown: [0, -1],
      };
      const d = nudge[e.key];
      if (d) {
        e.preventDefault();
        if (isStart) setStartCrop((c) => ({ x: c.x + d[0], y: c.y + d[1] }));
        else setEndCrop((c) => ({ x: c.x + d[0], y: c.y + d[1] }));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // handleSave closes over current state; React's eslint rule is fine with this since
    // we re-attach on every relevant update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStart, startPixel, endPixel, mode, postWidth, postHeight, onSave, onCancel]);

  const canSave =
    !!startPixel && (mode === "simple" || !!endPixel);

  return (
    <div
      className="fp-backdrop"
      style={{ display: "grid", placeItems: "center", padding: 24, zIndex: 100 }}
      onClick={onCancel}
    >
      <div
        className="fp-card fp-fade-in"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(720px, 100%)",
          display: "grid",
          gap: 12,
          padding: 16,
          boxShadow: "var(--shadow-lg)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>Crop for 9:16</div>
          <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
            Drag · Scroll/pinch to zoom · Arrows nudge · Esc cancels · Enter saves
          </div>
        </div>

        {/* Mode switch */}
        <div style={{ display: "flex", gap: 6 }}>
          <ModeChip
            active={mode === "simple"}
            onClick={() => setMode("simple")}
            title="One crop. The Reel auto-zooms gently within it."
          >
            Simple
          </ModeChip>
          <ModeChip
            active={mode === "director"}
            onClick={() => {
              setMode("director");
              setTab("start");
            }}
            title="Pick a Start and an End viewport. The Reel animates between them."
          >
            Director (animated pan)
          </ModeChip>
        </div>

        {/* Start / End sub-tabs (director mode only) */}
        {mode === "director" && (
          <div style={{ display: "flex", gap: 0, borderBottom: "0.5px solid var(--border)" }}>
            <SubTab active={tab === "start"} onClick={() => setTab("start")}>
              Start {startPixel && "✓"}
            </SubTab>
            <SubTab active={tab === "end"} onClick={() => setTab("end")}>
              End {endPixel && "✓"}
            </SubTab>
          </div>
        )}

        <div
          style={{
            position: "relative",
            width: "100%",
            aspectRatio: "16 / 12",
            background: "#000",
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
          {/* react-easy-crop's initialCroppedAreaPixels only takes effect on first
              mount, so wait until we know where to seed before mounting. We also key
              by tab so switching Start/End remounts with the right initial area. */}
          {currentInitialArea === undefined && !initialCrop ? (
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "grid",
                placeItems: "center",
                color: "var(--text-dim)",
                fontSize: 12,
              }}
            >
              Finding a good starting crop…
            </div>
          ) : (
            <Cropper
              key={`${tab}-${currentInitialArea?.x ?? 0}-${currentInitialArea?.y ?? 0}`}
              image={previewUrl(postId)}
              crop={currentCrop}
              zoom={currentZoom}
              aspect={ASPECT}
              onCropChange={isStart ? setStartCrop : setEndCrop}
              onZoomChange={isStart ? setStartZoom : setEndZoom}
              onCropComplete={onCropComplete}
              initialCroppedAreaPixels={currentInitialArea}
              showGrid={true}
              objectFit="contain"
              zoomSpeed={0.5}
              minZoom={0.5}
              maxZoom={4}
              restrictPosition={false}
            />
          )}
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ fontSize: 11, color: "var(--text-dim)" }}>Zoom</label>
          <input
            type="range"
            min={0.5}
            max={4}
            step={0.01}
            value={currentZoom}
            onChange={(e) =>
              isStart ? setStartZoom(Number(e.target.value)) : setEndZoom(Number(e.target.value))
            }
            style={{ flex: 1 }}
          />
          <button
            className="fp-btn-ghost"
            onClick={() => {
              if (isStart) {
                setStartCrop({ x: 0, y: 0 });
                setStartZoom(1);
              } else {
                setEndCrop({ x: 0, y: 0 });
                setEndZoom(1);
              }
            }}
            style={{ padding: "5px 10px", fontSize: 11 }}
          >
            Reset
          </button>
        </div>

        {mode === "director" && (
          <div
            style={{
              fontSize: 11,
              color: "var(--text-dim)",
              padding: "8px 10px",
              background: "var(--bg)",
              border: "0.5px solid var(--border)",
              borderRadius: 6,
            }}
          >
            {tab === "start"
              ? "Set where the camera begins. Tip: a wider crop here that pushes in tighter at the end reads as a reveal."
              : "Set where the camera lands. The Reel interpolates frame-by-frame from Start to End."}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <button className="fp-btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button className="fp-btn" disabled={!canSave} onClick={handleSave}>
            {mode === "director" ? "Save Start + End" : "Save crop"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ModeChip({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      style={{
        background: active ? "var(--teal-tint, rgba(93,202,165,0.1))" : "transparent",
        color: active ? "var(--teal, #5dcaa5)" : "var(--text-dim)",
        border: `0.5px solid ${active ? "rgba(93,202,165,0.3)" : "var(--border-strong)"}`,
        borderRadius: 999,
        padding: "5px 12px",
        fontSize: 12,
        fontWeight: 500,
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

function SubTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        background: "transparent",
        border: 0,
        borderBottom: active ? "2px solid var(--teal)" : "2px solid transparent",
        marginBottom: -1,
        padding: "8px 14px",
        fontSize: 12,
        fontWeight: active ? 500 : 400,
        color: active ? "var(--text)" : "var(--text-dim)",
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}
