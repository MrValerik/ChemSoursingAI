// Раздел «Настройки» (раздел 14 UI/UX-плана): пользователи и роли (RBAC),
// статус каналов. Доступен только администратору.

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ChannelStatus, UserAdminRead } from "../api/types";
import { ROLE_LABELS, useAuth } from "../auth/AuthContext";

export default function SettingsSection() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<UserAdminRead[]>([]);
  const [channels, setChannels] = useState<ChannelStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newFullName, setNewFullName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState("buyer");

  const load = async () => {
    try {
      const [u, c] = await Promise.all([api.listUsers(), api.channelsStatus()]);
      setUsers(u);
      setChannels(c);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const createUser = async () => {
    setBusy(true);
    try {
      await api.createUser({
        username: newUsername.trim(),
        full_name: newFullName.trim(),
        password: newPassword,
        role: newRole,
      });
      setCreateOpen(false);
      setNewUsername("");
      setNewFullName("");
      setNewPassword("");
      await load();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const patchUser = async (
    id: number,
    payload: { role?: string; is_active?: boolean },
  ) => {
    setBusy(true);
    try {
      await api.updateUser(id, payload);
      await load();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="requests-page">
      <div className="requests-header">
        <h1>Настройки</h1>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="panel">
        <div className="tab-toolbar">
          <h2>Пользователи и роли (RBAC)</h2>
          <button onClick={() => setCreateOpen((v) => !v)}>+ Пользователь</button>
        </div>

        {createOpen && (
          <div className="add-supplier">
            <div className="row">
              <div className="field">
                <label>Логин *</label>
                <input
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                />
              </div>
              <div className="field">
                <label>ФИО *</label>
                <input
                  value={newFullName}
                  onChange={(e) => setNewFullName(e.target.value)}
                />
              </div>
            </div>
            <div className="row">
              <div className="field">
                <label>Пароль * (мин. 6 символов)</label>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                />
              </div>
              <div className="field">
                <label>Роль</label>
                <select value={newRole} onChange={(e) => setNewRole(e.target.value)}>
                  {Object.entries(ROLE_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="actions">
              <button
                onClick={() => void createUser()}
                disabled={
                  busy ||
                  !newUsername.trim() ||
                  !newFullName.trim() ||
                  newPassword.length < 6
                }
              >
                Создать
              </button>
              <button className="secondary" onClick={() => setCreateOpen(false)}>
                Отмена
              </button>
            </div>
          </div>
        )}

        <table className="summary">
          <thead>
            <tr>
              <th>ФИО</th>
              <th>Логин</th>
              <th>Роль</th>
              <th>Статус</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={u.is_active ? "" : "row-muted"}>
                <td>{u.full_name}</td>
                <td>{u.username}</td>
                <td>
                  <select
                    value={u.role}
                    disabled={busy || u.id === me?.id}
                    onChange={(e) => void patchUser(u.id, { role: e.target.value })}
                  >
                    {Object.entries(ROLE_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <span className={`badge ${u.is_active ? "tone-ok" : "tone-neutral"}`}>
                    {u.is_active ? "активен" : "отключён"}
                  </span>
                </td>
                <td>
                  {u.id !== me?.id && (
                    <button
                      className="secondary btn-small"
                      disabled={busy}
                      onClick={() =>
                        void patchUser(u.id, { is_active: !u.is_active })
                      }
                    >
                      {u.is_active ? "Отключить" : "Включить"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Каналы</h2>
        <table className="summary">
          <thead>
            <tr>
              <th>Канал</th>
              <th>Состояние</th>
            </tr>
          </thead>
          <tbody>
            {channels.map((c) => (
              <tr key={c.channel}>
                <td>{c.title}</td>
                <td>
                  <span className={`badge ${c.configured ? "tone-ok" : "tone-warn"}`}>
                    {c.configured ? "настроен" : "не настроен"}
                  </span>{" "}
                  <span className="note">{c.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="note" style={{ marginTop: 8 }}>
          Параметры каналов задаются в .env на сервере; проверка соединения
          и журнал доступа (152-ФЗ) появятся на этапе интеграций.
        </p>
      </div>
    </div>
  );
}
