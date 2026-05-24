import { useEffect, useRef, useState } from "react";

import { previewUrl } from "../api/client";

type Props = {
  postId: string;
  caption?: string;
  meta?: string;
  onClose: () => void;
};

export default function Lightbox({ postId, caption, meta, onClose }: Props) {
  const [loaded, setLoaded] = useState(false);
  const [errored, setErrored] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Cache-hit fix: if the image is already complete by the time React attaches
  // refs (e.g. browser served from cache before the onLoad handler bound), the
  // onLoad event never fires and the img stays display:none. Promote to "loaded"
  // imperatively when we see complete=true with a non-zero natural size.
  useEffect(() => {
    const img = imgRef.current;
    if (img && img.complete && img.naturalWidth > 0) {
      setLoaded(true);
    }
  }, [postId]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.92)",
        display: "grid",
        placeItems: "center",
        zIndex: 200,
        cursor: "zoom-out",
      }}
    >
      <button
        onClick={onClose}
        aria-label="Close"
        style={{
          position: "absolute",
          top: 16,
          right: 20,
          background: "transparent",
          border: 0,
          color: "rgba(255,255,255,0.8)",
          fontSize: 28,
          cursor: "pointer",
          lineHeight: 1,
        }}
      >
        ×
      </button>

      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: "95vw",
          maxHeight: "95vh",
          display: "grid",
          placeItems: "center",
          gap: 12,
          cursor: "default",
          position: "relative",
        }}
      >
        {!loaded && !errored && (
          <div
            style={{
              position: "absolute",
              color: "rgba(255,255,255,0.6)",
              fontSize: 14,
              pointerEvents: "none",
            }}
          >
            Loading preview…
          </div>
        )}
        {errored && (
          <div style={{ color: "var(--danger)", fontSize: 14 }}>
            Couldn't load preview — original may have been purged or moved.
          </div>
        )}
        {!errored && (
          <img
            ref={imgRef}
            src={previewUrl(postId)}
            alt={caption ?? ""}
            onLoad={() => setLoaded(true)}
            onError={() => setErrored(true)}
            style={{
              maxWidth: "95vw",
              maxHeight: caption || meta ? "85vh" : "95vh",
              objectFit: "contain",
              borderRadius: 4,
              boxShadow: loaded ? "0 8px 40px rgba(0,0,0,0.4)" : "none",
              opacity: loaded ? 1 : 0,
              transition: "opacity 120ms ease",
            }}
          />
        )}
        {loaded && (caption || meta) && (
          <div style={{ textAlign: "center", color: "rgba(255,255,255,0.7)" }}>
            {caption && <div style={{ fontSize: 14 }}>{caption}</div>}
            {meta && <div style={{ fontSize: 11, color: "rgba(255,255,255,0.45)", marginTop: 2 }}>{meta}</div>}
          </div>
        )}
      </div>
    </div>
  );
}
