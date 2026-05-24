import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import PageHeader from "../components/PageHeader";
import Topbar from "../components/Topbar";
import { usePageTitle } from "../hooks/usePageTitle";
import SettingsPlatforms from "./SettingsPlatforms";
import SettingsTitleTemplates from "./SettingsTitleTemplates";
import SettingsAlbums from "./SettingsAlbums";
import SettingsFlickr from "./SettingsFlickr";
import SettingsGeneral from "./SettingsGeneral";
import SettingsGroups from "./SettingsGroups";
import SettingsActivity from "./SettingsActivity";
import SettingsAI from "./SettingsAI";
import SettingsImport from "./SettingsImport";
import SettingsProfiles from "./SettingsProfiles";
import SettingsSystem from "./SettingsSystem";

const TABS: { to: string; label: string; phase?: string }[] = [
  { to: "/settings/general", label: "General" },
  { to: "/settings/platforms", label: "Platforms" },
  { to: "/settings/flickr", label: "Flickr" },
  { to: "/settings/import", label: "Import" },
  { to: "/settings/profiles", label: "Tag Profiles" },
  { to: "/settings/title-templates", label: "Title Templates" },
  { to: "/settings/albums", label: "Albums" },
  { to: "/settings/groups", label: "Groups" },
  { to: "/settings/ai", label: "AI Tagging" },
  { to: "/settings/activity", label: "Activity" },
  { to: "/settings/system", label: "System" },
];

export default function Settings() {
  usePageTitle("Settings");
  return (
    <>
      <Topbar />
      <div className="fp-page fp-fade-in">
        <PageHeader
          title="Settings"
          subtitle="Configure account, integrations, profiles, and AI tagging"
        />
        <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 28, alignItems: "start" }}>
          <nav
            style={{
              display: "grid",
              gap: 2,
              position: "sticky",
              top: 80,
            }}
          >
            {TABS.map((t) => (
              <NavLink
                key={t.to}
                to={t.to}
                style={({ isActive }) => ({
                  padding: "8px 12px",
                  borderRadius: 7,
                  fontSize: 13,
                  background: isActive ? "var(--hover)" : "transparent",
                  color: isActive ? "var(--text)" : "var(--text-dim)",
                  fontWeight: isActive ? 500 : 400,
                  textDecoration: "none",
                  display: "flex",
                  justifyContent: "space-between",
                  transition: "background 120ms ease, color 120ms ease",
                })}
              >
                <span>{t.label}</span>
                {t.phase && (
                  <span style={{ fontSize: 11, color: "var(--text-fade)" }}>{t.phase}</span>
                )}
              </NavLink>
            ))}
          </nav>
          <div>
            <Routes>
              <Route index element={<Navigate to="general" replace />} />
              <Route path="general" element={<SettingsGeneral />} />
              <Route path="platforms" element={<SettingsPlatforms />} />
              <Route path="flickr" element={<SettingsFlickr />} />
              <Route path="import" element={<SettingsImport />} />
              <Route path="profiles" element={<SettingsProfiles />} />
              <Route path="title-templates" element={<SettingsTitleTemplates />} />
              <Route path="albums" element={<SettingsAlbums />} />
              <Route path="groups" element={<SettingsGroups />} />
              <Route path="ai" element={<SettingsAI />} />
              <Route path="activity" element={<SettingsActivity />} />
              <Route path="system" element={<SettingsSystem />} />
              <Route path="*" element={<Placeholder />} />
            </Routes>
          </div>
        </div>
      </div>
    </>
  );
}

function Placeholder() {
  return (
    <div className="fp-card">
      <div style={{ color: "var(--text-dim)" }}>This tab lands in a later phase.</div>
    </div>
  );
}
