import { useState } from "react";
import { api, ApiError, type RFQCreatePayload } from "../api/client";
import type { RFQRead } from "../api/types";

const ALL_INCOTERMS = ["CIP", "FCA", "EXW"];
const COUNTRY_OPTIONS = [
  "Китай",
  "Индия",
  "Турция",
  "Германия",
  "США",
];

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
  const [customCountry, setCustomCountry] = useState("");
  const [aiInstructions, setAiInstructions] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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

  const addCustomCountry = () => {
    const country = customCountry.trim();
    if (!country) return;
    setCountries((current) =>
      current.some((item) => item.toLocaleLowerCase() === country.toLocaleLowerCase())
        ? current
        : [...current, country],
    );
    setCustomCountry("");
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

      <div className="row">
        <div className="field">
          <label>CAS-номер</label>
          <input value={cas} onChange={(event) => setCas(event.target.value)} />
        </div>
        <div className="field">
          <label>Наименование вещества</label>
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </div>
      </div>

      <div className="row">
        <div className="field">
          <label>Чистота / грейд</label>
          <input value={purity} onChange={(event) => setPurity(event.target.value)} />
        </div>
        <div className="field">
          <label>Требуемый объём</label>
          <input value={volume} onChange={(event) => setVolume(event.target.value)} />
        </div>
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
          {countries
            .filter((country) => !COUNTRY_OPTIONS.includes(country))
            .map((country) => (
              <label key={country}>
                <input
                  type="checkbox"
                  checked
                  onChange={() => toggleCountry(country)}
                />
                {country}
              </label>
            ))}
        </div>
        <div className="inline-add">
          <input
            value={customCountry}
            placeholder="Добавить другую страну"
            onChange={(event) => setCustomCountry(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addCustomCountry();
              }
            }}
          />
          <button
            type="button"
            className="secondary"
            onClick={addCustomCountry}
            disabled={!customCountry.trim()}
          >
            Добавить
          </button>
        </div>
        {countries.length === 0 && (
          <span className="error">Выберите хотя бы одну страну.</span>
        )}
      </div>

      <div className="field">
        <label>Сколько поставщиков найти в каждой стране</label>
        <input
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
        <span className="note">
          ИИ-агент постарается найти указанное число подходящих компаний для
          каждой выбранной страны.
        </span>
      </div>

      <div className="field">
        <label>Применение</label>
        <textarea
          value={application}
          onChange={(event) => setApplication(event.target.value)}
        />
      </div>

      <div className="field">
        <label>Дополнительные требования для ИИ</label>
        <textarea
          maxLength={4000}
          placeholder="Например: искать только производителей фармацевтического грейда с GMP"
          value={aiInstructions}
          onChange={(event) => setAiInstructions(event.target.value)}
        />
        <span className="note">
          Эти требования будут применены к автоматическому поиску и последующей
          обработке ответов поставщиков.
        </span>
      </div>

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
