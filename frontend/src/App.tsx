import { useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import AppShell, { type SectionKey } from "./components/AppShell";
import Login from "./components/Login";
import Placeholder from "./components/Placeholder";
import ReviewQueue from "./components/ReviewQueue";
import RfqWorkspace from "./components/RfqWorkspace";
import SuppliersSection from "./components/SuppliersSection";
import TemplatesSection from "./components/TemplatesSection";

function Sections() {
  const { user, loading } = useAuth();
  const [section, setSection] = useState<SectionKey>("requests");
  // Переход из других разделов сразу в карточку запроса.
  const [jumpRfqId, setJumpRfqId] = useState<number | null>(null);

  const openRfq = (id: number) => {
    setJumpRfqId(id);
    setSection("requests");
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
        <Placeholder title="Дашборд" step="шаг 2" />
      )}
      {section === "requests" && (
        <RfqWorkspace
          jumpRfqId={jumpRfqId}
          onJumpConsumed={() => setJumpRfqId(null)}
        />
      )}
      {section === "suppliers" && <SuppliersSection />}
      {section === "review" && <ReviewQueue onOpenRfq={openRfq} />}
      {section === "templates" && <TemplatesSection />}
      {section === "settings" && (
        <Placeholder title="Настройки" step="шаг 6" />
      )}
    </AppShell>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Sections />
    </AuthProvider>
  );
}
