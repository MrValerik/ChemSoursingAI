import { useEffect, useState } from "react";
import { api, ApiError, type RFQCreatePayload } from "../api/client";
import type {
  AnalogVariation,
  IdentificationMethod,
  RFQRead,
  SubstanceRecord,
} from "../api/types";
import NameCandidates from "./NameCandidates";
import { Field, Input, Select, Textarea } from "./ui";

const ALL_INCOTERMS = ["CIP", "FCA", "EXW"];
const COUNTRY_OPTIONS = ["Россия", "Китай", "Индия"];

// Три способа задать предмет закупки. CAS-номер есть не у всего, что
// закупают: у смесей, рецептур и промышленных продуктов его нет и не
// будет, но отправить по ним запрос поставщику вполне можно.
const METHODS: {
  value: IdentificationMethod;
  label: string;
  hint: string;
}[] = [
  {
    value: "cas",
    label: "По CAS-номеру",
    hint: "Точная молекула. Самый надёжный поиск: номер уникален, название — нет.",
  },
  {
    value: "analog",
    label: "По аналогу",
    hint: "«Как вот это вещество, но с оговорками» — другой производитель, другая соль, другой грейд.",
  },
  {
    value: "spec",
    label: "По спецификации",
    hint: "Назначение и требования, когда конкретная молекула не важна: смеси, рецептуры, промышленные продукты.",
  },
];

// «Аналог» без границ означает для поставщика что угодно, и в ответ
// приходит не то, что просили.
const VARIATIONS: { value: AnalogVariation; label: string }[] = [
  { value: "salt", label: "другая соль, гидрат или эфир" },
  { value: "purity", label: "другая чистота или грейд" },
  { value: "form", label: "другая форма (порошок, гранулы, раствор)" },
  { value: "manufacturer", label: "любой производитель того же вещества" },
];

interface Props {
  onCreated: (rfq: RFQRead) => void;
}

export default function NewRfq({ onCreated }: Props) {
  const [method, setMethod] = useState<IdentificationMethod>("cas");
  const [cas, setCas] = useState("50-78-2");
  const [analogReference, setAnalogReference] = useState("");
  const [variations, setVariations] = useState<AnalogVariation[]>([
    "manufacturer",
  ]);
  const [specification, setSpecification] = useState("");
  const [synonyms, setSynonyms] = useState<string[]>([]);
  const [excludedNames, setExcludedNames] = useState<string[]>([]);
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
    identification_method: method,
    // Номер уходит только в своём режиме: в остальных он не задан и не
    // должен подставляться из прежнего значения поля.
    cas: method === "cas" ? cas.trim() : null,
    analog_reference: method === "analog" ? analogReference.trim() : null,
    analog_variations: method === "analog" ? variations : [],
    specification: method !== "cas" ? specification.trim() || null : null,
    confirmed_synonyms: synonyms,
    excluded_names: excludedNames,
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

  const toggleVariation = (value: AnalogVariation) =>
    setVariations((current) =>
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    );

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
      // Карточка справочника ключуется номером, поэтому выбор из него
      // всегда переводит запрос в режим поиска по CAS.
      setMethod("cas");
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

  // У каждого способа идентификации свой минимум данных: требовать
  // номер там, где его не бывает, значит закрыть закупку смесей.
  const methodReady =
    method === "cas"
      ? cas.trim().length > 0
      : method === "analog"
        ? analogReference.trim().length > 0
        : specification.trim().length > 0 || application.trim().length > 0;

  const canCreate =
    !busy &&
    methodReady &&
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
          value={substanceId != null ? String(substanceId) : ""}
          onChange={chooseSubstance}
          options={[
            { value: "", label: "Новое вещество — заполнить вручную" },
            ...substances.map((substance) => ({
              value: String(substance.id),
              label: `${substance.preferred_name} · CAS ${substance.cas}`,
            })),
          ]}
        />
      </Field>

      <div className="field">
        <label>Как задан предмет закупки</label>
        <div className="checks">
          {METHODS.map((item) => (
            <label key={item.value} title={item.hint}>
              <input
                type="radio"
                name="identification-method"
                checked={method === item.value}
                disabled={substanceId !== null}
                onChange={() => setMethod(item.value)}
              />
              {item.label}
            </label>
          ))}
        </div>
        <span className="note">
          {METHODS.find((item) => item.value === method)?.hint}
        </span>
      </div>

      <div className="row">
        {method === "cas" && (
          <Field label="CAS-номер">
            <Input
              disabled={substanceId !== null}
              value={cas}
              onChange={(event) => setCas(event.target.value)}
            />
          </Field>
        )}
        <Field
          label="Наименование вещества"
          hint={
            method === "cas"
              ? undefined
              : "Без номера название — единственный якорь поиска, поэтому пишите его так, как оно звучит у поставщиков."
          }
        >
          <Input
            disabled={substanceId !== null}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
      </div>

      {method === "analog" && (
        <>
          <Field
            label="На что должно быть похоже"
            hint="CAS-номер или название эталонного вещества."
          >
            <Input
              value={analogReference}
              placeholder="например, 107-43-7 или Glycine betaine"
              onChange={(event) => setAnalogReference(event.target.value)}
            />
          </Field>
          <div className="field">
            <label>Чем аналог может отличаться</label>
            <div className="checks">
              {VARIATIONS.map((item) => (
                <label key={item.value}>
                  <input
                    type="checkbox"
                    checked={variations.includes(item.value)}
                    onChange={() => toggleVariation(item.value)}
                  />
                  {item.label}
                </label>
              ))}
            </div>
            <span className="note">
              Без этих отметок «аналог» означает для поставщика сразу всё
              перечисленное, и в ответ приходит не то, что просили.
            </span>
          </div>
        </>
      )}

      {method !== "cas" && (
        <>
          <NameCandidates
            label="Другие названия того же вещества"
            hint="Без CAS-номера именно эти названия служат якорем поиска. Чем точнее список, тем меньше в выдаче чужих веществ."
            placeholder="например, Cocamidopropyl betaine"
            value={synonyms}
            onChange={setSynonyms}
          />
          <NameCandidates
            label="Похожие названия, которые НЕ подходят"
            hint="Соседние по названию вещества — другая соль, другой грейд. Они уйдут в отрицательный фильтр, иначе поиск найдёт настоящих поставщиков не того вещества."
            placeholder="например, Betaine hydrochloride"
            value={excludedNames}
            onChange={setExcludedNames}
          />
        </>
      )}

      {method !== "cas" && (
        <Field
          label={method === "analog" ? "Критерии эквивалентности" : "Требования к веществу"}
          hint={
            method === "analog"
              ? "Состав, INCI, активное содержание и другие показатели, которые аналог обязан повторять."
              : "То, по чему поставщик поймёт, что именно нужно: назначение, ключевые показатели, стандарт."
          }
        >
          <Textarea
            maxLength={4000}
            placeholder={
              method === "analog"
                ? "Например: INCI Silicone Quaternium-16 (and) Undeceth-11; активное содержание 22%; pH 6–8"
                : "Например: неионогенный загуститель для шампуня, вязкость 4000–6000 сП, pH 5–7"
            }
            value={specification}
            onChange={(event) => setSpecification(event.target.value)}
          />
        </Field>
      )}

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
