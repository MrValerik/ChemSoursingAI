// Контекст аутентификации: токен в localStorage, текущий пользователь, вход/выход.

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api, clearToken, getToken, setToken, setUnauthorizedHandler } from "../api/client";
import type { UserRead } from "../api/types";

interface AuthState {
  user: UserRead | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserRead | null>(null);
  const [loading, setLoading] = useState(true);

  const logout = () => {
    clearToken();
    setUser(null);
  };

  useEffect(() => {
    // Глобальный обработчик 401: токен протух — разлогиниваемся.
    setUnauthorizedHandler(logout);
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  const login = async (username: string, password: string) => {
    const resp = await api.login(username, password);
    setToken(resp.access_token);
    setUser(resp.user);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth вне AuthProvider");
  return ctx;
}

export const ROLE_LABELS: Record<string, string> = {
  buyer: "Закупщик",
  head: "Руководитель",
  admin: "Администратор",
  auditor: "Аудитор",
};
