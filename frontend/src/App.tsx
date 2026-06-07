import { useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import AppShell, { type SectionKey } from "./components/AppShell";
import Login from "./components/Login";
import Placeholder from "./components/Placeholder";
import RfqWorkspace from "./components/RfqWorkspace";

function Sections() {
  const { user, loading } = useAuth();
  const [section, setSection] = useState<SectionKey>("requests");

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
      {section === "requests" && <RfqWorkspace />}
      {section === "suppliers" && (
        <Placeholder title="Поставщики" step="шаг 5" />
      )}
      {section === "review" && (
        <Placeholder title="Ручной разбор" step="шаг 5" />
      )}
      {section === "templates" && (
        <Placeholder title="Шаблоны" step="шаг 5" />
      )}
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
