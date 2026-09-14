import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { UserPreferences } from "../api/types";

export default function UserPreferencesPanel({ readOnly = false }: { readOnly?: boolean }) {
  const [preferences, setPreferences] = useState<UserPreferences | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const load = () => {
    setError(null);
    void api.getPreferences().then(setPreferences).catch((e) => setError(String(e)));
  };
  useEffect(load, []);

  const save = async (enabled: boolean) => {
    setBusy(true);
    setSaved(false);
    setError(null);
    try {
      setPreferences(await api.savePreferences({ auto_dispatch_after_search: enabled }));
      setSaved(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return <section className="panel">
    <h2>Личные настройки</h2>
    <div className="settings-checks"><label>
      <input type="checkbox" checked={preferences?.auto_dispatch_after_search ?? false}
        disabled={readOnly || !preferences || busy} onChange={(e) => void save(e.target.checked)} />
      Автоматическая отправка рассылки компаниям после завершения поиска
    </label></div>
    <p className="note">Применяется к поискам, которые запускаете вы. После завершения
      проверки система отправит RFQ по Email подтверждённым кандидатам из короткого
      списка с доступным адресом. Повторные письма не отправляются. Включение разрешает
      отправку без отдельного подтверждения при работающем Email в режиме Live;
      в деморежиме внешние письма не отправляются. Права вашей роли сохраняются.</p>
    {!preferences && !error && <p role="status">Загрузка…</p>}
    {busy && <p role="status">Сохранение…</p>}
    {saved && <p role="status">Настройка сохранена</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {!preferences && error && <button onClick={load}>Повторить загрузку</button>}
  </section>;
}
