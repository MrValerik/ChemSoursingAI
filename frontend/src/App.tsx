import { useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import AppShell, { type SectionKey } from "./components/AppShell";
import ActivityReporter from "./components/ActivityReporter";
import CommunicationTesting from "./components/CommunicationTesting";
import Dashboard from "./components/Dashboard";
import Login from "./components/Login";
import ReviewQueue from "./components/ReviewQueue";
import RfqWorkspace from "./components/RfqWorkspace";
import SettingsSection from "./components/SettingsSection";
import SubstancesSection from "./components/SubstancesSection";
import SuppliersSection from "./components/SuppliersSection";
import TemplatesSection from "./components/TemplatesSection";
import PromptStudio from "./components/PromptStudio";

function Sections() {
  const { user, loading } = useAuth();
  const [section, setSection] = useState<SectionKey>("requests");
  // Переход из других разделов сразу в карточку запроса.
  const [jumpRfqId, setJumpRfqId] = useState<number | null>(null);
  const [jumpSubstanceId, setJumpSubstanceId] = useState<number | null>(null);

  const openRfq = (id: number) => {
    setJumpRfqId(id);
    setSection("requests");
  };

  const openSubstance = (id: number) => {
    setJumpSubstanceId(id);
    setSection("substances");
  };

  if (loading) {
    return <div className="app-loading note">Загрузка…</div>;
  }
  if (!user) {
    return <Login />;
  }

  return (
    <AppShell section={section} onSectionChange={setSection}>
      {section === "dashboard" && (
        <Dashboard onOpenRfq={openRfq} onGoToRequests={() => setSection("requests")} />
      )}
      {section === "requests" && (
        <RfqWorkspace
          jumpRfqId={jumpRfqId}
          onJumpConsumed={() => setJumpRfqId(null)}
          onOpenSubstance={openSubstance}
        />
      )}
      {section === "substances" && (
        <SubstancesSection
          focusId={jumpSubstanceId}
          onFocusConsumed={() => setJumpSubstanceId(null)}
        />
      )}
      {section === "suppliers" && <SuppliersSection onOpenRfq={openRfq} />}
      {section === "review" && <ReviewQueue onOpenRfq={openRfq} />}
      {section === "templates" && <TemplatesSection />}
      {section === "prompts" && <PromptStudio />}
      {section === "communication-testing" && <CommunicationTesting />}
      {section === "settings" && <SettingsSection />}
    </AppShell>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ActivityReporter />
      <Sections />
    </AuthProvider>
  );
}
