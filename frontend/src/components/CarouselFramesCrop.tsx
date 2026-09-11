import { useQuery } from "@tanstack/react-query";

import { fetchCarouselPosts } from "../api/client";
import IgCropStudioFilmstrip from "./IgCropFilmstrip";

/**
 * Open the crop filmstrip on a carousel, by id rather than by selection.
 *
 * The filmstrip itself takes a list of posts and has done since before carousels were a
 * persisted concept. The gap it left was reach: a carousel's frames go `posted` the
 * moment it publishes, and the draft queue lists exactly (pending AND no scheduled_at),
 * so from then on the only way back to a frame's crop was editing the database by hand.
 * Fetching by carousel id is deliberately indifferent to post status.
 */
export default function CarouselFramesCrop({
  carouselId,
  onClose,
}: {
  carouselId: string;
  onClose: () => void;
}) {
  const { data: posts, isLoading, isError, error } = useQuery({
    queryKey: ["carousel-posts", carouselId],
    queryFn: () => fetchCarouselPosts(carouselId),
  });

  if (isLoading || isError || !posts?.length) {
    return (
      <div
        role="dialog"
        aria-label="Carousel frames"
        style={{
          position: "fixed", inset: 0, zIndex: 60, display: "grid", placeItems: "center",
          background: "rgba(0,0,0,0.55)",
        }}
        onClick={onClose}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            background: "var(--panel)", border: "0.5px solid var(--border)",
            borderRadius: 10, padding: "18px 22px", maxWidth: 380,
            fontSize: 13, color: "var(--text)",
          }}
        >
          {isLoading ? (
            "Loading the carousel's frames…"
          ) : (
            <>
              <div style={{ marginBottom: 10 }}>
                {isError
                  ? `Couldn't load this carousel: ${(error as Error)?.message ?? "unknown error"}`
                  : "This carousel has no frames — it may have been ungrouped."}
              </div>
              <button className="fp-btn" onClick={onClose}>Close</button>
            </>
          )}
        </div>
      </div>
    );
  }

  return <IgCropStudioFilmstrip posts={posts} onClose={onClose} />;
}
