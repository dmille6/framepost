import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  type AIStatus,
  type AITestResult,
  fetchAIStatus,
  testAIProvider,
  updateAISettings,
} from "../api/client";
import ConfigField from "../components/ConfigField";
import { SkeletonRows } from "../components/Skeleton";

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic — Claude Haiku 4.5 vision",
  openai: "OpenAI — GPT-4o mini vision",
  both: "Both (ensemble) — runs both providers in parallel and merges",
};

export default function SettingsAI() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["ai-status"], queryFn: fetchAIStatus });
  const [testResults, setTestResults] = useState<Record<string, AITestResult | null>>({});

  const save = useMutation({
    mutationFn: updateAISettings,
    onSuccess: (next) => {
      qc.setQueryData(["ai-status"], next);
    },
  });

  const test = useMutation({
    mutationFn: testAIProvider,
    onSuccess: (result, provider) => {
      setTestResults((prev) => ({ ...prev, [provider]: result }));
    },
    onError: (e, provider) => {
      setTestResults((prev) => ({
        ...prev,
        [provider]: {
          ok: false,
          model: null,
          echo: null,
          error: e instanceof ApiError ? e.message : "test failed",
        },
      }));
    },
  });

  if (isLoading || !data) {
    return (
      <div className="fp-card" style={{ display: "grid", gap: 12, maxWidth: 720 }}>
        <SkeletonRows count={5} />
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <ProvidersPanel
        status={data}
        onSave={save.mutate}
        onTest={test.mutate}
        testResults={testResults}
        testing={test.isPending ? (test.variables as string) : null}
      />
      <BehaviorPanel status={data} onSave={save.mutate} saving={save.isPending} />
      <PrivacyPanel />
    </div>
  );
}

function ProvidersPanel({
  status,
  onSave,
  onTest,
  testResults,
  testing,
}: {
  status: AIStatus;
  onSave: (body: Parameters<typeof updateAISettings>[0]) => void;
  onTest: (provider: string) => void;
  testResults: Record<string, AITestResult | null>;
  testing: string | null;
}) {
  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 720 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 500 }}>AI provider</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          Pick which API to use for tag suggestions. Either or both keys can live in
          <code style={{ margin: "0 4px", fontSize: 12 }}>.env</code> — only the selected
          provider is called.
        </div>
      </div>

      <ConfigField label="Selected provider">
        <select
          className="fp-select"
          value={status.provider}
          onChange={(e) => onSave({ provider: e.target.value as "anthropic" | "openai" | "both" })}
        >
          <option value="anthropic">{PROVIDER_LABELS.anthropic}</option>
          <option value="openai">{PROVIDER_LABELS.openai}</option>
          <option value="both">{PROVIDER_LABELS.both}</option>
        </select>
        <div style={{ fontSize: 11, color: "var(--text-fade)", marginTop: 6 }}>
          Ensemble mode needs both keys. Tags supplied by both providers are pre-marked
          ★ in suggestions; tags from one are tagged A or O so you can pick at a glance.
        </div>
      </ConfigField>

      <div style={{ display: "grid", gap: 12 }}>
        {Object.entries(status.providers).map(([key, info]) => {
          const result = testResults[key];
          const isTesting = testing === key;
          return (
            <div
              key={key}
              style={{
                background: "var(--bg)",
                border: "0.5px solid var(--border)",
                borderRadius: 8,
                padding: 12,
                display: "grid",
                gap: 8,
              }}
            >
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
                <div>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{PROVIDER_LABELS[key] ?? key}</span>
                  <span
                    style={{
                      marginLeft: 8,
                      fontSize: 12,
                      color: info.configured ? "var(--teal)" : "var(--text-fade)",
                    }}
                  >
                    {info.configured ? "● key set" : "○ key missing"}
                  </span>
                  {key === status.provider && (
                    <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-dim)" }}>
                      (active)
                    </span>
                  )}
                </div>
                <button
                  className="fp-btn-ghost"
                  onClick={() => onTest(key)}
                  disabled={!info.configured || isTesting}
                  style={{ padding: "4px 10px", fontSize: 12 }}
                >
                  {isTesting ? "Testing…" : "Test"}
                </button>
              </div>
              {!info.configured && (
                <div style={{ fontSize: 11, color: "var(--text-fade)" }}>
                  Add{" "}
                  <code>{key === "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY"}</code>{" "}
                  to <code>.env</code> and restart the backend.
                </div>
              )}
              {result && (
                <div
                  style={{
                    fontSize: 12,
                    color: result.ok ? "var(--teal)" : "var(--danger)",
                  }}
                >
                  {result.ok
                    ? `OK · ${result.model} · echo: ${result.echo}`
                    : result.error}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function BehaviorPanel({
  status,
  onSave,
  saving,
}: {
  status: AIStatus;
  onSave: (body: Parameters<typeof updateAISettings>[0]) => void;
  saving: boolean;
}) {
  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 720 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 500 }}>Behaviour</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          Suggestions are opt-in. When enabled, you can fetch suggestions from the metadata
          editor on demand. Auto-apply runs the suggester at import time.
        </div>
      </div>

      <Toggle
        label="AI tagging enabled"
        sub="Without this, the metadata editor's Suggest button stays inert."
        checked={status.enabled}
        onChange={(v) => onSave({ enabled: v })}
        disabled={saving}
      />
      <Toggle
        label="Auto-apply on import"
        sub="Run the suggester on every new import. Adds latency + API cost — review suggestions before saving regardless."
        checked={status.auto_apply}
        onChange={(v) => onSave({ auto_apply: v })}
        disabled={saving}
      />
      <Toggle
        label="Suggest a description"
        sub="When IPTC caption is empty, ask the model for a 1–2 sentence draft."
        checked={status.suggest_description}
        onChange={(v) => onSave({ suggest_description: v })}
        disabled={saving}
      />
      <Toggle
        label="Send full resolution"
        sub="Off by default — sends a 1024-px preview. On = sends the full image (slower, more expensive, more data leaving the host)."
        checked={status.send_full_resolution}
        onChange={(v) => onSave({ send_full_resolution: v })}
        disabled={saving}
      />

      <ConfigField label="Max suggestions per image">
        <input
          className="fp-input"
          type="number"
          min={1}
          max={50}
          value={status.max_suggestions}
          onChange={(e) => onSave({ max_suggestions: Number(e.target.value) })}
        />
      </ConfigField>

      <ConfigField
        label="Description tone"
        hint="Concise = factual, journalistic (Reuters-style). Descriptive = evocative, atmospheric. Concise works better when you give the model proper-noun context (performer, venue, etc.) in the title."
      >
        <select
          className="fp-select"
          value={status.tone}
          onChange={(e) => onSave({ tone: e.target.value as "concise" | "descriptive" })}
          disabled={saving}
        >
          <option value="concise">Concise — factual</option>
          <option value="descriptive">Descriptive — atmospheric</option>
        </select>
      </ConfigField>
    </div>
  );
}

function PrivacyPanel() {
  return (
    <div
      className="fp-card"
      style={{
        maxWidth: 720,
        background: "#22180d",
        borderColor: "rgba(240,201,122,0.3)",
        fontSize: 13,
        display: "grid",
        gap: 8,
      }}
    >
      <div style={{ fontWeight: 500, color: "#f0c97a" }}>Privacy disclosure</div>
      <div style={{ color: "var(--text-dim)" }}>
        When AI tagging is enabled, FramePost sends a downscaled preview of each photo to
        the selected provider's servers (Anthropic or OpenAI). Originals are never sent
        unless you explicitly enable "Send full resolution" above. No metadata is uploaded
        beyond the image itself.
      </div>
      <div style={{ color: "var(--text-fade)", fontSize: 12 }}>
        AI tagging is opt-in and configurable per post — you can always edit or reject
        suggestions before scheduling.
      </div>
    </div>
  );
}

function Toggle({
  label,
  sub,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  sub?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label
      style={{
        display: "flex",
        gap: 12,
        alignItems: "flex-start",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        style={{ marginTop: 3 }}
      />
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13 }}>{label}</div>
        {sub && (
          <div style={{ fontSize: 11, color: "var(--text-fade)", marginTop: 2 }}>{sub}</div>
        )}
      </div>
    </label>
  );
}
