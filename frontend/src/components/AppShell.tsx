// Каркас приложения (раздел 2 UI/UX-плана): левое меню разделов,
// верхняя панель с поиском, уведомлениями и профилем/ролью.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ROLE_LABELS, useAuth } from "../auth/AuthContext";
import type { UserRole } from "../api/types";

export type SectionKey =
  | "dashboard"
  | "requests"
  | "suppliers"
  | "review"
  | "templates"
  | "settings";

interface NavItem {
  key: SectionKey;
  label: string;
  roles: UserRole[];
}

// Видимость разделов по ролям (раздел 4 плана: матрица доступа).
const NAV_ITEMS: NavItem[] = [
  { key: "dashboard", label: "Дашборд", roles: ["buyer", "head", "admin", "auditor"] },
  { key: "requests", label: "Запросы", roles: ["buyer", "head", "admin", "auditor"] },
  { key: "suppliers", label: "Поставщики", roles: ["buyer", "head", "auditor"] },
  { key: "review", label: "Ручной разбор", roles: ["buyer", "head", "auditor"] },
  { key: "templates", label: "Шаблоны", roles: ["buyer", "head", "admin", "auditor"] },
  { key: "settings", label: "Настройки", roles: ["admin"] },
];

export default function AppShell({
  section,
  onSectionChange,
  children,
}: {
  section: SectionKey;
  onSectionChange: (s: SectionKey) => void;
  children: ReactNode;
}) {
  const { user, logout } = useAuth();
  const [profileOpen, setProfileOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Закрытие выпадающих меню по клику вне.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setProfileOpen(false);
        setNotifOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  if (!user) return null;
  const visible = NAV_ITEMS.filter((i) => i.roles.includes(user.role));

  return (
    <div className="shell">
      <nav className="shell-nav">
        <div className="shell-logo">ChemSource AI</div>
        {visible.map((item) => (
          <button
            key={item.key}
            className={`nav-item ${section === item.key ? "active" : ""}`}
            onClick={() => onSectionChange(item.key)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className="shell-body">
        <header className="shell-topbar" ref={menuRef}>
          <input
            className="topbar-search"
            placeholder="Поиск: CAS / вещество / поставщик"
            title="Глобальный поиск (в разработке)"
          />
          <div className="topbar-right">
            <div className="topbar-menu">
              <button
                className="icon-btn"
                title="Уведомления"
                onClick={() => {
                  setNotifOpen((v) => !v);
                  setProfileOpen(false);
                }}
              >
                🔔
              </button>
              {notifOpen && (
                <div className="dropdown">
                  <div className="dropdown-title">Уведомления</div>
                  <div className="dropdown-empty note">
                    Нет новых уведомлений
                  </div>
                </div>
              )}
            </div>
            <div className="topbar-menu">
              <button
                className="profile-btn"
                onClick={() => {
                  setProfileOpen((v) => !v);
                  setNotifOpen(false);
                }}
              >
                <span className="profile-name">{user.full_name}</span>
                <span className="profile-role">{ROLE_LABELS[user.role]}</span>
                <span className="caret">▾</span>
              </button>
              {profileOpen && (
                <div className="dropdown">
                  <div className="dropdown-title">{user.username}</div>
                  <button className="dropdown-item" disabled title="В разработке">
                    Сменить пароль
                  </button>
                  <button className="dropdown-item" onClick={logout}>
                    Выйти
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        <div className="shell-content">{children}</div>
      </div>
    </div>
  );
}
