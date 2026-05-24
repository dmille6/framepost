import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth";
import { usePageTitle } from "../hooks/usePageTitle";

export default function Login() {
  usePageTitle("Sign in");
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation() as { state?: { from?: string } };
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (user) {
    return null;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(location.state?.from ?? "/drafts", { replace: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 32,
        background:
          "radial-gradient(ellipse at 50% 0%, rgba(93, 202, 165, 0.06), transparent 60%), var(--bg)",
      }}
    >
      <form
        onSubmit={onSubmit}
        className="fp-card fp-fade-in"
        style={{
          padding: 32,
          width: 380,
          display: "grid",
          gap: 20,
          boxShadow: "var(--shadow-lg)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 4 }}>
          <Logomark />
          <div>
            <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: "-0.01em" }}>
              frame<span style={{ color: "var(--teal)" }}>post</span>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
              Sign in to continue
            </div>
          </div>
        </div>

        <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>
          USERNAME
          <input
            className="fp-input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            autoComplete="username"
            required
          />
        </label>
        <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>
          PASSWORD
          <input
            className="fp-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && (
          <div
            style={{
              color: "var(--danger)",
              fontSize: 13,
              padding: "8px 12px",
              background: "var(--danger-tint)",
              border: "0.5px solid rgba(245, 156, 156, 0.2)",
              borderRadius: 8,
            }}
          >
            {error}
          </div>
        )}
        <button type="submit" disabled={submitting} className="fp-btn" style={{ padding: "11px 14px" }}>
          {submitting && <span className="fp-spinner" />}
          {submitting ? "Signing in" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

function Logomark() {
  return (
    <svg width="40" height="40" viewBox="0 0 32 32" aria-hidden focusable="false">
      <rect width="32" height="32" rx="8" fill="#0f0f0f" stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />
      <circle cx="16" cy="16" r="9" fill="none" stroke="#5dcaa5" strokeWidth="1.6" />
      <circle cx="16" cy="16" r="2.6" fill="#5dcaa5" />
      <path d="M16 7.4 L18.6 11.6 L13.4 11.6 Z" fill="#5dcaa5" opacity="0.85" />
      <path d="M24.6 16 L20.4 18.6 L20.4 13.4 Z" fill="#5dcaa5" opacity="0.85" />
      <path d="M16 24.6 L13.4 20.4 L18.6 20.4 Z" fill="#5dcaa5" opacity="0.85" />
      <path d="M7.4 16 L11.6 13.4 L11.6 18.6 Z" fill="#5dcaa5" opacity="0.85" />
    </svg>
  );
}
