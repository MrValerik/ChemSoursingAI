// Экран входа (раздел 3 UI/UX-плана). SSO/LDAP — опционально, позже.

import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";

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
        <h1>ChemSource AI</h1>
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

        <button type="submit" disabled={busy || !username || !password}>
          {busy ? "Вход…" : "Войти"}
        </button>
        <p className="note login-hint">SSO / LDAP — по согласованию (см. ТЗ M9)</p>
      </form>
    </div>
  );
}
