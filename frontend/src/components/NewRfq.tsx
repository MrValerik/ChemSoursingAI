import { useEffect, useState } from "react";
import { api, ApiError, type RFQCreatePayload } from "../api/client";
import type { RFQRead, SubstanceRecord } from "../api/types";
import { Field, Input, Select, Textarea } from "./ui";

const ALL_INCOTERMS = ["CIP", "FCA", "EXW"];
const COUNTRY_OPTIONS = ["Россия", "Китай", "Индия"];

interface Props {
  onCreated: (rfq: RFQRead) => void;
}

export default function NewRfq({ onCreated }: Props) {
  const [cas, setCas] = useState("50-78-2");
  const [name, setName] = useState("Ацетилсалициловая кислота");
  const [purity, setPurity] = useState("USP");
  const [application, setApplication] = useState("");
  const [volume, setVolume] = useState("500 kg");
  const [incoterms, setIncoterms] = useState<string[]>(["CIP", "FCA", "EXW"]);
  const [countries, setCountries] = useState<string[]>(["Китай"]);
  const [supplierTarget, setSupplierTarget] = useState(5);
  const [aiInstructions, setAiInstructions] = useState("");
  const [substances, setSubstances] = useState<SubstanceRecord[]>([]);
  const [substanceId, setSubstanceId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listSubstances().then(setSubstances).catch(() => setSubstances([]));
  }, []);

  const payload = (): RFQCreatePayload => ({
    cas: cas.trim(),
    name: name.trim(),
    incoterms,
    purity: purity.trim() || null,
    application: application.trim() || null,
    volume: volume.trim() || null,
    channels: ["email"],
    search_countries: countries,
    supplier_target: supplierTarget,
    substance_id: substanceId,
    additional_instructions: aiInstructions.trim() || null,
  });

  const toggleIncoterm = (code: string) =>
    setIncoterms((current) =>
      current.includes(code)
        ? current.filter((item) => item !== code)
        : [...current, code],
    );

  const toggleCountry = (country: string) =>
    setCountries((current) =>
      current.includes(country)
        ? current.filter((item) => item !== country)
        : [...current, country],
    );

  const chooseSubstance = (value: string) => {
    const id = value ? Number(value) : null;
    setSubstanceId(id);
    if (id === null) return;
    const selected = substances.find((item) => item.id === id);
    if (selected) {
      setCas(selected.cas);
      setName(selected.preferred_name);
    }
  };

  const onCreate = async () => {
    setBusy(true);
    setError(null);
    try {
      const request = await api.createRfq(payload(), true, true);
      onCreated(request);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const canCreate =
    !busy &&
    cas.trim().length > 0 &&
    name.trim().length > 0 &&
    countries.length > 0 &&
    incoterms.length > 0;

  return (
    <div className="panel">
      <h2>Создать новый запрос</h2>
      <p className="note">
        После создания система сразу проверит вещество и поставит поиск по
        выбранным странам в очередь. Можно сразу создавать следующий запрос.
      </p>

      <Field
        label="Вещество из справочника"
        hint="Если карточка уже существует, её подтверждённые названия и исключения автоматически применятся в поиске."
      >
        <Select
          value={substanceId ?? ""}
          onChange={(event) => chooseSubstance(event.target.value)}
        >
          <option value="">Новое вещество — заполнить вручную</option>
          {substances.map((substance) => (
            <option key={substance.id} value={substance.id}>
              {substance.preferred_name} · CAS {substance.cas}
            </option>
          ))}
        </Select>
      </Field>

      <div className="row">
        <Field label="CAS-номер">
          <Input
            disabled={substanceId !== null}
            value={cas}
            onChange={(event) => setCas(event.target.value)}
          />
        </Field>
        <Field label="Наименование вещества">
          <Input
            disabled={substanceId !== null}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
      </div>

      <div className="row">
        <Field label="Чистота / грейд">
          <Input value={purity} onChange={(event) => setPurity(event.target.value)} />
        </Field>
        <Field label="Требуемый объём">
          <Input value={volume} onChange={(event) => setVolume(event.target.value)} />
        </Field>
      </div>

      <div className="field">
        <label>Страны поиска</label>
        <div className="checks">
          {COUNTRY_OPTIONS.map((country) => (
            <label key={country}>
              <input
                type="checkbox"
                checked={countries.includes(country)}
                onChange={() => toggleCountry(country)}
              />
              {country}
            </label>
          ))}
        </div>
        {countries.length === 0 && (
          <span className="error">Выберите хотя бы одну страну.</span>
        )}
      </div>

      <Field
        className="compact-field"
        label="Сколько поставщиков найти в каждой стране"
        hint="ИИ-агент постарается найти указанное число подходящих компаний для каждой выбранной страны."
      >
        <Input
          type="number"
          min={1}
          max={20}
          value={supplierTarget}
          onChange={(event) =>
            setSupplierTarget(
              Math.min(20, Math.max(1, Number(event.target.value) || 1)),
            )
          }
        />
      </Field>

      <Field label="Применение">
        <Textarea
          value={application}
          onChange={(event) => setApplication(event.target.value)}
        />
      </Field>

      <Field
        label="Дополнительные требования для ИИ"
        hint="Эти требования будут применены к автоматическому поиску и последующей обработке ответов поставщиков."
      >
        <Textarea
          maxLength={4000}
          placeholder="Например: искать только производителей фармацевтического грейда с GMP"
          value={aiInstructions}
          onChange={(event) => setAiInstructions(event.target.value)}
        />
      </Field>

      <div className="field">
        <label>Условия поставки</label>
        <div className="checks">
          {ALL_INCOTERMS.map((code) => (
            <label key={code}>
              <input
                type="checkbox"
                checked={incoterms.includes(code)}
                onChange={() => toggleIncoterm(code)}
              />
              {code}
            </label>
          ))}
        </div>
      </div>

      <div className="actions">
        <button onClick={() => void onCreate()} disabled={!canCreate}>
          {busy ? "Создаём и ставим поиск в очередь…" : "Создать запрос и начать поиск"}
        </button>
      </div>

      {error && <p className="error">Ошибка: {error}</p>}
    </div>
  );
}
