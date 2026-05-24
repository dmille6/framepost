// Per-photo crop selector for the Reels builder. Locked 9:16 aspect.
//
// react-easy-crop reports coords in pixels of the displayed image. Since we display the
// 1600-px preview (not the full original — could be 60MP RAW-export), we scale the
// coordinates up to original-image space before passing them back to the parent.

import { useCallback, useEffect, useMemo, useState } from "react";
import Cropper, { type Area } from "react-easy-crop";

import { previewUrl, type ReelCrop } from "../api/client";

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

type Props = {
  postId: string;
  postWidth: number;
  postHeight: number;
  initialCrop: ReelCrop | null;
  onSave: (crop: ReelCrop) => void;
  onCancel: () => void;
};

export default function CropModal({
  postId,
  postWidth,
  postHeight,
  initialCrop,
  onSave,
  onCancel,
}: Props) {
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [pixelCrop, setPixelCrop] = useState<Area | null>(null);

  const initialAreaPixels = useMemo(
    () =>
      initialCrop
        ? originalCropToPreviewArea(initialCrop, postWidth, postHeight)
        : undefined,
    [initialCrop, postWidth, postHeight],
  );

  const onCropComplete = useCallback((_area: Area, areaPixels: Area) => {
    setPixelCrop(areaPixels);
  }, []);

  // Arrow-key 1px nudges. react-easy-crop's `crop` state is in displayed-image space
  // relative to the viewport center, but a 1-unit nudge is roughly 1px at zoom=1.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onCancel();
        return;
      }
      if (e.key === "Enter") {
        if (pixelCrop) {
          onSave(scaleCropToOriginal(pixelCrop, postWidth, postHeight));
        }
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
        setCrop((c) => ({ x: c.x + d[0], y: c.y + d[1] }));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pixelCrop, postWidth, postHeight, onSave, onCancel]);

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
            Drag to pan · Scroll/pinch to zoom · Arrows nudge · Esc cancels · Enter saves
          </div>
        </div>

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
          <Cropper
            image={previewUrl(postId)}
            crop={crop}
            zoom={zoom}
            aspect={ASPECT}
            onCropChange={setCrop}
            onZoomChange={setZoom}
            onCropComplete={onCropComplete}
            initialCroppedAreaPixels={initialAreaPixels}
            showGrid={true}
            objectFit="contain"
            zoomSpeed={0.5}
            minZoom={0.5}
            maxZoom={4}
            restrictPosition={false}
          />
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ fontSize: 11, color: "var(--text-dim)" }}>Zoom</label>
          <input
            type="range"
            min={0.5}
            max={4}
            step={0.01}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
            style={{ flex: 1 }}
          />
          <button
            className="fp-btn-ghost"
            onClick={() => {
              setCrop({ x: 0, y: 0 });
              setZoom(1);
            }}
            style={{ padding: "5px 10px", fontSize: 11 }}
          >
            Reset
          </button>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <button className="fp-btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="fp-btn"
            disabled={!pixelCrop}
            onClick={() => {
              if (pixelCrop) onSave(scaleCropToOriginal(pixelCrop, postWidth, postHeight));
            }}
          >
            Save crop
          </button>
        </div>
      </div>
    </div>
  );
}
