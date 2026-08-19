// Каркас приложения (раздел 2 UI/UX-плана): левое меню разделов,
// верхняя панель с поиском, уведомлениями и профилем/ролью.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { LogoMark, LogoWord } from "./Logo";
import { applyTheme, readTheme, type Theme } from "../theme";
import { ROLE_LABELS, useAuth } from "../auth/AuthContext";
import type { UserRole } from "../api/types";
import { Icon, IconButton } from "./ui";

export type SectionKey =
  | "requests"
  | "substances"
  | "suppliers"
  | "intermediaries"
  | "review"
  | "templates"
  | "prompts"
  | "communication-testing"
  | "feedback"
  | "settings";

interface NavItem {
  key: SectionKey;
  label: string;
  roles: UserRole[];
  // Прижать к низу колонки: раздел нужен редко и не должен занимать место
  // в ежедневном списке.
  atBottom?: boolean;
}

// Видимость разделов по ролям (раздел 4 плана: матрица доступа).
const NAV_ITEMS: NavItem[] = [
  { key: "requests", label: "Запросы", roles: ["buyer", "head", "admin", "auditor"] },
  { key: "substances", label: "Химические вещества", roles: ["buyer", "head", "admin", "auditor"] },
  { key: "suppliers", label: "Поставщики", roles: ["buyer", "head", "admin", "auditor"] },
  {
    key: "intermediaries",
    label: "Посредники",
    roles: ["buyer", "head", "admin", "auditor"],
  },
  { key: "review", label: "Ручной разбор", roles: ["buyer", "head", "auditor"] },
  { key: "templates", label: "Шаблоны", roles: ["admin"] },
  { key: "prompts", label: "ИИ-промпты", roles: ["admin"] },
  { key: "communication-testing", label: "Тестирование общения", roles: ["admin"] },
  // Написать может кто угодно, включая аудитора: он читает программу
  // внимательнее прочих и первым замечает, чего в ней не хватает.
  {
    key: "feedback",
    label: "Обратная связь",
    roles: ["buyer", "head", "admin", "auditor"],
    atBottom: true,
  },
  { key: "settings", label: "Настройки", roles: ["admin"], atBottom: true },
];

// Раздел из адресной строки может не подойти текущей роли — например, ссылку
// на «Настройки» открыл закупщик. Матрица доступа одна и живёт здесь.
export function isSectionAllowed(section: SectionKey, role: UserRole) {
  return NAV_ITEMS.some((item) => item.key === section && item.roles.includes(role));
}

// Замок ставится по самой матрице, а не списком ключей: добавится раздел с
// доступом только для администратора — пометка появится сама.
const isAdminOnly = (item: NavItem) =>
  item.roles.length === 1 && item.roles[0] === "admin";

export default function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const [theme, setTheme] = useState<Theme>(readTheme);
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
        <div className="shell-logo">
          <LogoMark size={26} />
          <LogoWord />
        </div>
        {visible.map((item) => (
          <NavLink
            key={item.key}
            to={`/${item.key}`}
            className={({ isActive }) =>
              `nav-item${item.atBottom ? " is-bottom" : ""}${isActive ? " active" : ""}`
            }
            title={
              isAdminOnly(item)
                ? `${item.label} — раздел виден только администратору`
                : undefined
            }
          >
            <span>{item.label}</span>
            {isAdminOnly(item) && (
              <Icon
                name="lock"
                size={13}
                className="nav-item-lock"
                aria-label="Только для администратора"
              />
            )}
          </NavLink>
        ))}
      </nav>

      <div className="shell-body">
        <header className="shell-topbar" ref={menuRef}>
          <div className="topbar-right">
            <IconButton
              className="icon-btn"
              icon={theme === "dark" ? "sun" : "moon"}
              label={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
              title={theme === "dark" ? "Переключить на светлую тему" : "Переключить на тёмную тему"}
              onClick={() => {
                const next = theme === "dark" ? "light" : "dark";
                applyTheme(next);
                setTheme(next);
              }}
            />
            <div className="topbar-menu">
              <IconButton
                className="icon-btn"
                icon="bell"
                label="Центр уведомлений"
                title="Завершение поиска, ответы поставщиков и задачи ручной проверки"
                onClick={() => {
                  setNotifOpen((v) => !v);
                  setProfileOpen(false);
                }}
              />
              {notifOpen && (
                <div className="dropdown notification-dropdown">
                  <div className="dropdown-title">Центр уведомлений</div>
                  <p className="notification-description">
                    Здесь появятся завершённые поиски, ошибки ИИ-агентов, задачи
                    ручной проверки, новые ответы поставщиков и котировки.
                  </p>
                  <div className="dropdown-empty note">
                    Новых событий нет
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
