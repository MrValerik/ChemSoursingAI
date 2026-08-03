// Экран входа (раздел 3 UI/UX-плана). SSO/LDAP — опционально, позже.

import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { LogoMark, LogoWord } from "./Logo";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={onSubmit}>
        <div className="login-brand">
          <LogoMark size={40} />
          <LogoWord />
        </div>
        <p className="note">Рабочее место отдела закупок</p>

        <div className="field">
          <label htmlFor="login-username">Логин</label>
          <input
            id="login-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
          />
        </div>
        <div className="field">
          <label htmlFor="login-password">Пароль</label>
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>

        {error && <p className="error">{error}</p>}

        <button
          type="submit"
          className={busy ? "is-busy" : undefined}
          aria-busy={busy}
          disabled={busy || !username || !password}
        >
          {busy && <span className="loading-spinner on-brand" aria-hidden="true" />}
          {busy ? "Проверяем данные…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
