import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider, RequireAuth } from "./auth";
import Activity from "./pages/Activity";
import Analytics from "./pages/Analytics";
import DraftQueue from "./pages/DraftQueue";
import Login from "./pages/Login";
import Published from "./pages/Published";
import Scheduled from "./pages/Scheduled";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<Navigate to="/drafts" replace />} />
        <Route path="/login" element={<Login />} />
        <Route path="/drafts" element={<RequireAuth><DraftQueue /></RequireAuth>} />
        <Route path="/scheduled" element={<RequireAuth><Scheduled /></RequireAuth>} />
        <Route path="/published" element={<RequireAuth><Published /></RequireAuth>} />
        <Route path="/activity" element={<RequireAuth><Activity /></RequireAuth>} />
        <Route path="/analytics" element={<RequireAuth><Analytics /></RequireAuth>} />
        <Route path="/settings/*" element={<RequireAuth><Settings /></RequireAuth>} />
      </Routes>
    </AuthProvider>
  );
}
