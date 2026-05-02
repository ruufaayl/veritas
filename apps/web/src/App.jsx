import { Route, Routes } from "react-router-dom";

import AuditFlow from "./pages/AuditFlow.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Landing from "./pages/Landing.jsx";
import ReportView from "./pages/ReportView.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/audit" element={<AuditFlow />} />
      <Route path="/report/:auditId" element={<ReportView />} />
    </Routes>
  );
}
