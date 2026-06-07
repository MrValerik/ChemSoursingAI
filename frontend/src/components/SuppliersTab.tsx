// Вкладка «Поставщики» (раздел 9 UI/UX-плана): кандидаты из реестра,
// чекбоксы получателей, ручное добавление, переход к рассылке.
// Веб-сорсинг открытых источников появится на этапе интеграций.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ChannelKind, RecipientRead, SupplierRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const TYPE_LABELS: Record<string, string> = {
  manufacturer: "производитель",
  distributor: "дистрибьютор",
};

function Stars({ value }: { value: string | null }) {
  const n = Number(value);
  if (!value || Number.isNaN(n)) return <span className="note">{value ?? "—"}</span>;
  return (
    <span className="stars" title={`Репутация: ${n} из 5`}>
      {"★".repeat(Math.max(0, Math.min(5, Math.round(n))))}
      <span className="stars-empty">{"★".repeat(Math.max(0, 5 - Math.round(n)))}</span>
    </span>
  );
}

export default function SuppliersTab({
  rfqId,
  onGoToDispatch,
}: {
  rfqId: number;
  onGoToDispatch: () => void;
}) {
  const { user } = useAuth();
  const readOnly = user?.role === "auditor";

  const [suppliers, setSuppliers] = useState<SupplierRead[]>([]);
  const [recipients, setRecipients] = useState<RecipientRead[]>([]);
  const [checked, setChecked] = useState<Map<number, ChannelKind>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [addOpen, setAddOpen] = useState(false);
  const [newCompany, setNewCompany] = useState("");
  const [newType, setNewType] = useState("manufacturer");
  const [newEmail, setNewEmail] = useState("");
  const [newWhatsapp, setNewWhatsapp] = useState("");

  const load = async () => {
    try {
      const [s, r] = await Promise.all([
        api.listSuppliers(),
        api.listRecipients(rfqId),
      ]);
      setSuppliers(s);
      setRecipients(r);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfqId]);

  const alreadySelected = useMemo(
    () => new Set(recipients.map((r) => r.supplier_id)),
    [recipients],
  );

  const toggle = (s: SupplierRead) => {
    if (readOnly || alreadySelected.has(s.id) || s.channels.length === 0) return;
    setChecked((prev) => {
      const next = new Map(prev);
      if (next.has(s.id)) next.delete(s.id);
      else next.set(s.id, s.channels[0]);
      return next;
    });
  };

  const setChannel = (id: number, channel: ChannelKind) => {
    setChecked((prev) => new Map(prev).set(id, channel));
  };

  const submitSelection = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.selectRecipients(
        rfqId,
        [...checked.entries()].map(([supplier_id, channel]) => ({
          supplier_id,
          channel,
        })),
      );
      setChecked(new Map());
      onGoToDispatch();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const addSupplier = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.addSupplier({
        company: newCompany.trim(),
        type: newType,
        email: newEmail.trim() || null,
        whatsapp: newWhatsapp.trim() || null,
      });
      setAddOpen(false);
      setNewCompany("");
      setNewEmail("");
      setNewWhatsapp("");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <div className="tab-toolbar">
        <h2>Поставщики для рассылки</h2>
        <div className="requests-actions">
          <button className="secondary" onClick={() => void load()}>
            Обновить поиск
          </button>
          {!readOnly && (
            <button className="secondary" onClick={() => setAddOpen((v) => !v)}>
              Добавить вручную
            </button>
          )}
        </div>
      </div>
      <p className="note">
        Кандидаты — из реестра поставщиков; веб-сорсинг открытых источников
        будет подключён на этапе интеграций.
      </p>

      {addOpen && (
        <div className="add-supplier">
          <div className="row">
            <div className="field">
              <label>Компания *</label>
              <input value={newCompany} onChange={(e) => setNewCompany(e.target.value)} />
            </div>
            <div className="field">
              <label>Тип</label>
              <select value={newType} onChange={(e) => setNewType(e.target.value)}>
                <option value="manufacturer">производитель</option>
                <option value="distributor">дистрибьютор</option>
              </select>
            </div>
          </div>
          <div className="row">
            <div className="field">
              <label>Email</label>
              <input value={newEmail} onChange={(e) => setNewEmail(e.target.value)} />
            </div>
            <div className="field">
              <label>WhatsApp</label>
              <input value={newWhatsapp} onChange={(e) => setNewWhatsapp(e.target.value)} />
            </div>
          </div>
          <div className="actions">
            <button onClick={() => void addSupplier()} disabled={busy || !newCompany.trim()}>
              Сохранить
            </button>
            <button className="secondary" onClick={() => setAddOpen(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      <table className="summary">
        <thead>
          <tr>
            <th></th>
            <th>Компания</th>
            <th>Тип</th>
            <th>Канал</th>
            <th>Источник</th>
            <th>Репутация</th>
          </tr>
        </thead>
        <tbody>
          {suppliers.map((s) => {
            const selected = alreadySelected.has(s.id);
            const isChecked = checked.has(s.id);
            return (
              <tr
                key={s.id}
                className={selected ? "row-muted" : "clickable"}
                onClick={() => toggle(s)}
              >
                <td onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={isChecked || selected}
                    disabled={readOnly || selected || s.channels.length === 0}
                    onChange={() => toggle(s)}
                  />
                </td>
                <td>
                  <div>{s.company}</div>
                  {s.certificates && s.certificates.length > 0 && (
                    <div className="cas">{s.certificates.join(", ")}</div>
                  )}
                </td>
                <td>{s.type ? TYPE_LABELS[s.type] : "—"}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  {s.channels.length === 0 && <span className="note">нет контакта</span>}
                  {isChecked && s.channels.length > 1 ? (
                    <select
                      value={checked.get(s.id)}
                      onChange={(e) => setChannel(s.id, e.target.value as ChannelKind)}
                    >
                      {s.channels.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  ) : (
                    s.channels.join(", ")
                  )}
                </td>
                <td>{s.source ?? "—"}</td>
                <td>
                  <Stars value={s.reputation} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="tab-footer">
        <span className="note">
          Выбрано: {checked.size}
          {alreadySelected.size > 0 ? ` · уже в рассылке: ${alreadySelected.size}` : ""}
        </span>
        <button
          onClick={() => void submitSelection()}
          disabled={busy || checked.size === 0}
        >
          Перейти к рассылке →
        </button>
      </div>
    </div>
  );
}
