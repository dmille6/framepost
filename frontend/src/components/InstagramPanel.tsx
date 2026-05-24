import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchInstagramFormat,
  instagramImageUrl,
  markInstagramPosted,
} from "../api/client";
import { absoluteTime, relativeTime } from "../lib/time";
import CopyableBox from "./CopyableBox";
import InstagramEngagementTracker from "./InstagramEngagementTracker";
import { CardHeader } from "./PageHeader";
import { SkeletonRows } from "./Skeleton";

type Format = "square" | "portrait";
type Fit = "pad" | "crop";
type Bg = "black" | "white";

type Props = {
  postId: string;
};

export default function InstagramPanel({ postId }: Props) {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["instagram-format", postId],
    queryFn: () => fetchInstagramFormat(postId),
  });

  const [fmt, setFmt] = useState<Format>("portrait");
  const [fit, setFit] = useState<Fit>("pad");
  const [bg, setBg] = useState<Bg>("black");
  const [tagsInComment, setTagsInComment] = useState(false);

  const mark = useMutation({
    mutationFn: ({ posted }: { posted: boolean }) => markInstagramPosted(postId, posted),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["instagram-format", postId] });
      void qc.invalidateQueries({ queryKey: ["post", postId] });
      void qc.invalidateQueries({ queryKey: ["published"] });
    },
  });

  if (isLoading) return <SkeletonRows count={4} height={36} />;
  if (error) return <div style={{ color: "var(--danger)" }}>{(error as Error).message}</div>;
  if (!data) return null;

  const hashtagBlock = data.hashtags.join(" ");
  const captionWithTags = tagsInComment
    ? data.caption
    : data.caption + (hashtagBlock ? "\n\n" + hashtagBlock : "");

  const captionLen = captionWithTags.length;
  const captionWarn = captionLen > 2200;
  const tagCount = data.hashtags.length;
  const posted = !!data.posted_to_instagram_at;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <CardHeader
        title="Instagram"
        subtitle={
          posted ? (
            <span title={absoluteTime(data.posted_to_instagram_at)}>
              Marked posted to Instagram {relativeTime(data.posted_to_instagram_at)}
            </span>
          ) : (
            "Copy-paste a polished post — caption, hashtags, and an IG-sized image."
          )
        }
        action={
          <button
            onClick={() => mark.mutate({ posted: !posted })}
            className={posted ? "fp-btn-ghost" : "fp-btn"}
            disabled={mark.isPending}
            style={{ padding: "7px 12px", fontSize: 12 }}
            title={posted ? "Remove the IG-posted flag" : "Mark this photo as posted to Instagram"}
          >
            {posted ? "Unmark posted" : "Mark posted"}
          </button>
        }
      />

      {/* Caption + hashtags */}
      <div style={{ display: "grid", gap: 12 }}>
        <BlockHeader
          label="Caption"
          help={tagsInComment ? "Click to copy. Paste tags into the first comment." : "Click to copy. Hashtags included at the end."}
          length={captionLen}
          warn={captionWarn}
          warnText="Over IG's 2,200-char cap"
        />
        <CopyableBox text={captionWithTags}>
          <pre
            style={{
              margin: 0,
              padding: "10px 44px 10px 12px",
              background: "var(--bg)",
              border: "0.5px solid var(--border)",
              borderRadius: 8,
              fontFamily: "inherit",
              fontSize: 13,
              lineHeight: 1.5,
              color: "var(--text)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 280,
              overflow: "auto",
            }}
          >
            {captionWithTags || "(empty)"}
          </pre>
        </CopyableBox>

        {tagsInComment && hashtagBlock && (
          <>
            <BlockHeader label="First-comment hashtags" help={`${tagCount} of 30`} />
            <CopyableBox text={hashtagBlock}>
              <div
                style={{
                  padding: "10px 44px 10px 12px",
                  background: "var(--bg)",
                  border: "0.5px solid var(--border)",
                  borderRadius: 8,
                  fontSize: 13,
                  lineHeight: 1.6,
                color: "var(--teal)",
                wordBreak: "break-word",
              }}
            >
              {hashtagBlock}
            </div>
            </CopyableBox>
          </>
        )}

        <label
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            fontSize: 12,
            color: "var(--text-dim)",
            cursor: "pointer",
            userSelect: "none",
          }}
        >
          <input
            type="checkbox"
            checked={tagsInComment}
            onChange={(e) => setTagsInComment(e.target.checked)}
          />
          Hashtags as first comment (cleaner caption)
        </label>

        {!data.signature && (
          <div style={{ fontSize: 11, color: "var(--text-fade)" }}>
            Tip: set <strong>Settings → General → Instagram signature</strong> to auto-append a
            studio line (e.g. "📷 Darrell Miller Photography") to every caption.
          </div>
        )}
      </div>

      {/* Image format + download */}
      <div
        style={{
          padding: 16,
          border: "0.5px solid var(--border)",
          borderRadius: 10,
          background: "var(--bg)",
          display: "grid",
          gap: 12,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600 }}>Image</div>

        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <FieldGroup label="Aspect">
            <Segmented
              value={fmt}
              onChange={(v) => setFmt(v as Format)}
              options={[
                { value: "portrait", label: "4:5 portrait" },
                { value: "square", label: "1:1 square" },
              ]}
            />
          </FieldGroup>
          <FieldGroup label="Fit">
            <Segmented
              value={fit}
              onChange={(v) => setFit(v as Fit)}
              options={[
                { value: "pad", label: "Pad" },
                { value: "crop", label: "Crop" },
              ]}
            />
          </FieldGroup>
          {fit === "pad" && (
            <FieldGroup label="Background">
              <Segmented
                value={bg}
                onChange={(v) => setBg(v as Bg)}
                options={[
                  { value: "black", label: "Black" },
                  { value: "white", label: "White" },
                ]}
              />
            </FieldGroup>
          )}
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            gap: 14,
            alignItems: "center",
          }}
        >
          <div
            style={{
              width: 120,
              height: fmt === "portrait" ? 150 : 120,
              borderRadius: 8,
              border: "0.5px solid var(--border-strong)",
              overflow: "hidden",
              background: bg === "white" && fit === "pad" ? "#ffffff" : "#000000",
            }}
          >
            <img
              key={`${fmt}-${fit}-${bg}`}
              src={instagramImageUrl(postId, fmt, fit, bg)}
              alt=""
              style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
            />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <a
              href={instagramImageUrl(postId, fmt, fit, bg)}
              download
              className="fp-btn"
              style={{ padding: "9px 14px", fontSize: 13, justifyContent: "center" }}
            >
              Download {fmt === "portrait" ? "1080×1350" : "1080×1080"}
            </a>
            <div style={{ fontSize: 11, color: "var(--text-fade)" }}>
              {fit === "pad"
                ? "Whole image preserved, letterboxed."
                : "Center-cropped to fill the frame."}
            </div>
          </div>
        </div>
      </div>

      {/* Manual engagement tracker — only show after the user has marked the post as
          posted to IG, since otherwise the section doesn't make sense. */}
      {posted && <InstagramEngagementTracker postId={postId} />}
    </div>
  );
}

function BlockHeader({
  label,
  help,
  length,
  warn,
  warnText,
}: {
  label: string;
  help?: string;
  length?: number;
  warn?: boolean;
  warnText?: string;
}) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>{label}</span>
      {help && <span style={{ fontSize: 11, color: "var(--text-fade)" }}>{help}</span>}
      {typeof length === "number" && (
        <span
          style={{
            fontSize: 11,
            color: warn ? "var(--danger)" : "var(--text-fade)",
            fontVariantNumeric: "tabular-nums",
          }}
          title={warn ? warnText : undefined}
        >
          {length}/2200
        </span>
      )}
    </div>
  );
}

function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <span
        style={{
          fontSize: 11,
          color: "var(--text-fade)",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          fontWeight: 500,
        }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

function Segmented({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div
      style={{
        display: "inline-flex",
        border: "0.5px solid var(--border-strong)",
        borderRadius: 8,
        overflow: "hidden",
      }}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            onClick={() => onChange(opt.value)}
            style={{
              background: active ? "var(--hover)" : "transparent",
              color: active ? "var(--text)" : "var(--text-dim)",
              border: 0,
              padding: "6px 12px",
              fontSize: 12,
              fontWeight: active ? 500 : 400,
              cursor: "pointer",
              transition: "background 120ms ease, color 120ms ease",
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
