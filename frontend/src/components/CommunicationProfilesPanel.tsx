import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type {
  CommunicationProfile,
  CommunicationProfileVersion,
  UserAdminRead,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";

const FIELD_LABELS: Record<string, string> = {
  price: "Цена",
  currency: "Валюта",
  incoterm: "Incoterm",
  moq: "MOQ",
  grade: "Грейд / чистота",
  payment_terms: "Условия оплаты",
  lead_time: "Срок",
  specification: "CoA или TDS",
};

export default function CommunicationProfilesPanel() {
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const canSeeUsers = user?.role === "admin" || user?.role === "head";
  const [profiles, setProfiles] = useState<CommunicationProfile[]>([]);
  const [selected, setSelected] = useState<CommunicationProfile | null>(null);
  const [draft, setDraft] = useState<CommunicationProfile | null>(null);
  const [versions, setVersions] = useState<CommunicationProfileVersion[]>([]);
  const [users, setUsers] = useState<UserAdminRead[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = (profile: CommunicationProfile) => {
    setSelected(profile);
    setDraft({ ...profile, required_fields: [...profile.required_fields] });
    void api
      .communicationProfileVersions(profile.id)
      .then(setVersions)
      .catch(() => setVersions([]));
  };

  const load = async () => {
    const [profileItems, userItems] = await Promise.all([
      api.listCommunicationProfiles(),
      canSeeUsers ? api.listUsers() : Promise.resolve([] as UserAdminRead[]),
    ]);
    setProfiles(profileItems);
    setUsers(userItems);
    const fresh = profileItems.find((item) => item.id === selected?.id) ?? profileItems[0];
    if (fresh) open(fresh);
  };

  useEffect(() => {
    void load().catch((caught) => setError(String(caught)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!selected || !draft) return;
    setBusy(true);
    setError(null);
    try {
      await api.updateCommunicationProfile(selected.id, {
        name: draft.name,
        description: draft.description,
        system_instructions: draft.system_instructions,
        required_fields: draft.required_fields,
        max_input_chars: draft.max_input_chars,
        max_auto_replies: draft.max_auto_replies,
        max_duration_minutes: draft.max_duration_minutes,
        max_prompt_tokens: draft.max_prompt_tokens,
        max_completion_tokens: draft.max_completion_tokens,
        max_estimated_cost_usd: draft.max_estimated_cost_usd,
        is_active: draft.is_active,
      });
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    const suffix = Date.now().toString(36);
    setBusy(true);
    setError(null);
    try {
      const created = await api.createCommunicationProfile({
        slug: `custom-${suffix}`,
        name: "Новый профиль",
        description: "Настройте цель и безопасные лимиты общения.",
        system_instructions:
          "Собирай только необходимые для этой роли сведения и передавай неоднозначные решения человеку.",
        required_fields: ["price", "currency", "grade"],
        max_input_chars: 8000,
        max_auto_replies: 8,
        max_duration_minutes: 4320,
        max_prompt_tokens: 40000,
        max_completion_tokens: 8000,
        max_estimated_cost_usd: 6,
      });
      await load();
      open(created);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const assignUser = async (userId: number, profileId: number | null) => {
    setBusy(true);
    setError(null);
    try {
      await api.assignUserCommunicationProfile(userId, profileId);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const updateDraft = <K extends keyof CommunicationProfile>(
    key: K,
    value: CommunicationProfile[K],
  ) => setDraft((current) => (current ? { ...current, [key]: value } : current));

  return (
    <div className="panel" style={{ marginTop: 18 }}>
      <div className="tab-toolbar">
        <div>
          <h2>Профили общения</h2>
          <p className="note">
            Роль определяет, какие данные собирает агент. Запрет на заказ, оплату,
            договор и обход правил не меняется профилем.
          </p>
        </div>
        {canEdit && <button onClick={() => void create()}>+ Новый профиль</button>}
      </div>
      {error && <p className="error">{error}</p>}
      <div className="suppliers-layout">
        <div className="templates-list">
          {profiles.map((profile) => (
            <button
              className={`panel rfq-list-item ${selected?.id === profile.id ? "row-active" : ""}`}
              key={profile.id}
              onClick={() => open(profile)}
              type="button"
            >
              <span>{profile.name} · v{profile.version}</span>
              <span className="cas">{profile.description ?? profile.slug}</span>
            </button>
          ))}
        </div>
        {draft && (
          <div>
            <div className="field">
              <label>Название</label>
              <input disabled={!canEdit} value={draft.name} onChange={(event) => updateDraft("name", event.target.value)} />
            </div>
            <div className="field">
              <label>Цель профиля</label>
              <textarea disabled={!canEdit} rows={5} value={draft.system_instructions} onChange={(event) => updateDraft("system_instructions", event.target.value)} />
            </div>
            <div className="field">
              <label>Обязательные сведения</label>
              <div className="stack-inline">
                {Object.entries(FIELD_LABELS).map(([field, label]) => (
                  <label key={field}>
                    <input
                      type="checkbox"
                      disabled={!canEdit}
                      checked={draft.required_fields.includes(field)}
                      onChange={(event) =>
                        updateDraft(
                          "required_fields",
                          event.target.checked
                            ? [...draft.required_fields, field]
                            : draft.required_fields.filter((item) => item !== field),
                        )
                      }
                    />{" "}{label}
                  </label>
                ))}
              </div>
            </div>
            <div className="form-grid">
              {([
                ["max_input_chars", "Символов во входе"],
                ["max_auto_replies", "Автоответов"],
                ["max_duration_minutes", "Минут на диалог"],
                ["max_prompt_tokens", "Входных токенов"],
                ["max_completion_tokens", "Выходных токенов"],
                ["max_estimated_cost_usd", "Бюджет, USD"],
              ] as const).map(([field, label]) => (
                <div className="field" key={field}>
                  <label>{label}</label>
                  <input type="number" disabled={!canEdit} value={draft[field]} onChange={(event) => updateDraft(field, Number(event.target.value))} />
                </div>
              ))}
            </div>
            {canEdit && (
              <button disabled={busy || draft.required_fields.length === 0} onClick={() => void save()}>
                Сохранить новую версию
              </button>
            )}
            <h3>История версий</h3>
            {versions.map((version) => (
              <div className="rfq-list-item" key={version.id}>
                <span>v{version.version}</span>
                <span className="note">{version.changed_by ?? "—"} · {new Date(version.created_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {canSeeUsers && users.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <h3>Профили сотрудников</h3>
          {users.map((item) => (
            <div className="rfq-list-item" key={item.id}>
              <span>{item.full_name}</span>
              <select
                disabled={!canEdit || busy}
                value={item.communication_profile_id ?? ""}
                onChange={(event) => void assignUser(item.id, event.target.value ? Number(event.target.value) : null)}
              >
                <option value="">Закупщик по умолчанию</option>
                {profiles.filter((profile) => profile.is_active).map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.name} · v{profile.version}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
