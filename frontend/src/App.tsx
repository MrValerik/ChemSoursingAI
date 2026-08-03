import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import AppShell, { isSectionAllowed, type SectionKey } from "./components/AppShell";
import IntermediariesSection from "./components/IntermediariesSection";
import ActivityReporter from "./components/ActivityReporter";
import CommunicationTesting from "./components/CommunicationTesting";
import Login from "./components/Login";
import ReviewQueue from "./components/ReviewQueue";
import RfqWorkspace from "./components/RfqWorkspace";
import SettingsSection from "./components/SettingsSection";
import SubstancesSection from "./components/SubstancesSection";
import SuppliersSection from "./components/SuppliersSection";
import TemplatesSection from "./components/TemplatesSection";
import PromptStudio from "./components/PromptStudio";

// Адрес приходит извне — из закладки, из чужой ссылки, из истории браузера.
// Поэтому раздел сверяется с матрицей доступа роли, а не только с таблицей
// маршрутов: ссылка на «Настройки» не должна открыть их закупщику.
function RequireSection({
  section,
  children,
}: {
  section: SectionKey;
  children: React.ReactNode;
}) {
  const { user } = useAuth();
  if (!user) return null;
  if (!isSectionAllowed(section, user.role)) return <Navigate to="/requests" replace />;
  return <>{children}</>;
}

const SECTION_ELEMENTS: { path: string; section: SectionKey; element: React.ReactNode }[] = [
  { path: "/substances", section: "substances", element: <SubstancesSection /> },
  { path: "/substances/:substanceId", section: "substances", element: <SubstancesSection /> },
  { path: "/suppliers", section: "suppliers", element: <SuppliersSection /> },
  { path: "/intermediaries", section: "intermediaries", element: <IntermediariesSection /> },
  { path: "/review", section: "review", element: <ReviewQueue /> },
  { path: "/templates", section: "templates", element: <TemplatesSection /> },
  { path: "/prompts", section: "prompts", element: <PromptStudio /> },
  {
    path: "/communication-testing",
    section: "communication-testing",
    element: <CommunicationTesting />,
  },
  { path: "/settings", section: "settings", element: <SettingsSection /> },
];

// Запросы описаны отдельно: у раздела есть вложенные адреса — форма создания,
// карточка запроса и открытая в ней вкладка.
const REQUEST_PATHS = [
  "/requests",
  "/requests/new",
  "/requests/:rfqId",
  "/requests/:rfqId/:tab",
];

function Sections() {
  const { user, loading } = useAuth();

  if (loading) {
    return <div className="app-loading note">Загрузка…</div>;
  }
  if (!user) {
    return <Login />;
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/requests" replace />} />
        {REQUEST_PATHS.map((path) => (
          <Route key={path} path={path} element={<RfqWorkspace />} />
        ))}
        {SECTION_ELEMENTS.map(({ path, section, element }) => (
          <Route
            key={path}
            path={path}
            element={<RequireSection section={section}>{element}</RequireSection>}
          />
        ))}
        {/* Неизвестный адрес — не ошибка, а устаревшая закладка. */}
        <Route path="*" element={<Navigate to="/requests" replace />} />
      </Routes>
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
