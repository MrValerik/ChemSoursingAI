import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type RFQCreatePayload } from "../api/client";
import type {
  AnalogVariation,
  IdentificationMethod,
  RFQListItem,
  RFQRead,
} from "../api/types";
import NameCandidates from "./NameCandidates";
import { isValidCas, normalizeCas, suggestCheckDigit } from "./cas";
import { STATUS_LABELS } from "./statusLabels";
import { Field, Input, Textarea } from "./ui";

const ALL_INCOTERMS = ["CIP", "FCA", "EXW"];
const COUNTRY_OPTIONS = ["Россия", "Китай", "Индия"];

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
  const [cas, setCas] = useState("50-78-2");
  const [analogAccepted, setAnalogAccepted] = useState(false);
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
  const [pastRequests, setPastRequests] = useState<RFQListItem[]>([]);
  const [copiedFrom, setCopiedFrom] = useState<RFQListItem | null>(null);
  const [copyBusy, setCopyBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Прошлые запросы — то же самое, что раньше давал справочник, только
  // вместе с условиями закупки, а не одним названием.
  useEffect(() => {
    api.listRfqs().then(setPastRequests).catch(() => setPastRequests([]));
  }, []);

  // Номер распознаётся по контрольной цифре, а не по выбору закупщика: он
  // либо есть в поле, либо его нет, и спрашивать об этом отдельно незачем.
  const casEntered = cas.trim();
  const casNormalized = normalizeCas(casEntered);
  const casValid = isValidCas(casNormalized);
  const casSuggestion = casEntered ? suggestCheckDigit(casNormalized) : null;
  // Название, в которое вставили номер: предложить перенос дешевле, чем
  // молча искать по строке, которая для поиска бесполезна.
  const nameIsCas = isValidCas(normalizeCas(name));

  // Способ идентификации остаётся в контракте поиска — он строит разные
  // запросы, — но выводится из заполненного, а не спрашивается у человека.
  const method: IdentificationMethod = analogAccepted
    ? "analog"
    : casValid
      ? "cas"
      : "spec";

  const payload = (): RFQCreatePayload => ({
    identification_method: method,
    // Непрошедший проверку номер не уходит в запрос: форма к этому моменту
    // уже не даёт создать запрос с таким полем.
    cas: casValid ? casNormalized : null,
    // Эталон аналога — то самое вещество, которое назвали выше: отдельного
    // «на что должно быть похоже» в живых запросах не было ни разу.
    analog_reference: analogAccepted ? name.trim() : null,
    analog_variations: analogAccepted ? variations : [],
    specification: specification.trim() || null,
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
    // Карточку справочника форма больше не привязывает: связь ставится
    // при подтверждении идентичности в самом поиске.
    substance_id: null,
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

  // Похожие запросы ищутся по тому же, что закупщик уже вводит: номер
  // совпадает целиком, название — по вхождению. Отдельного поля для этого
  // не нужно.
  const nameQuery = name.trim().toLocaleLowerCase();
  const suggestions = useMemo(() => {
    const ranked: { item: RFQListItem; rank: number }[] = [];
    for (const item of pastRequests) {
      const itemCas = normalizeCas(item.cas || "");
      const itemName = item.name.trim().toLocaleLowerCase();
      if (casNormalized && itemCas && itemCas === casNormalized) {
        ranked.push({ item, rank: 0 });
      } else if (nameQuery.length >= 3 && itemName.includes(nameQuery)) {
        ranked.push({ item, rank: 1 });
      } else if (
        itemName.length >= 3 &&
        nameQuery.length >= 3 &&
        nameQuery.includes(itemName)
      ) {
        ranked.push({ item, rank: 2 });
      }
    }
    ranked.sort(
      (a, b) =>
        a.rank - b.rank || b.item.created_at.localeCompare(a.item.created_at),
    );
    return ranked.slice(0, 5).map((entry) => entry.item);
  }, [pastRequests, casNormalized, nameQuery]);

  // Перенос параметров прошлого запроса. Копируются условия закупки целиком:
  // повторная закупка того же вещества отличается объёмом, а не базисами и
  // не списком названий.
  const copyFromRequest = async (item: RFQListItem) => {
    setCopyBusy(true);
    setError(null);
    try {
      const [source, ai] = await Promise.all([
        api.getRfq(item.id),
        api.getRfqAiSettings(item.id).catch(() => null),
      ]);
      setName(source.name);
      setCas(source.cas || "");
      setAnalogAccepted(source.identification_method === "analog");
      setVariations((source.analog_variations || []) as AnalogVariation[]);
      setSpecification(source.specification || "");
      setSynonyms(source.confirmed_synonyms || []);
      setExcludedNames(source.excluded_names || []);
      setPurity(source.purity || "");
      setApplication(source.application || "");
      setVolume(source.volume || "");
      if (source.incoterms?.length) setIncoterms(source.incoterms);
      if (source.search_countries?.length) setCountries(source.search_countries);
      setSupplierTarget(source.supplier_target);
      setAiInstructions(ai?.additional_instructions || "");
      setCopiedFrom(item);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setCopyBusy(false);
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

  // Минимум запроса — название: номера может не быть у смесей, рецептур и
  // промышленных продуктов, а искать по названию можно всегда. Заполненный
  // номер обязан быть верным — иначе он уводит поиск к другому веществу.
  const canCreate =
    !busy &&
    name.trim().length > 0 &&
    (!casEntered || casValid) &&
    countries.length > 0 &&
    incoterms.length > 0;

  return (
    <div className={`new-rfq${suggestions.length > 0 ? " has-suggestions" : ""}`}>
      <div className="panel">
        <h2>Создать новый запрос</h2>
        <p className="note">
          После создания система сразу проверит вещество и поставит поиск по
          выбранным странам в очередь. Можно сразу создавать следующий запрос.
        </p>

        <div className="row">
          <Field
            label="Что нужно закупить"
            hint="Название, марка или торговое наименование — так, как его пишут поставщики. Это якорь поиска, и он нужен всегда."
          >
            <Input
              value={name}
              placeholder="например, Ацетилсалициловая кислота или Dowsil 556"
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field
            label="CAS-номер, если известен"
            hint="Номера нет у смесей, рецептур и промышленных продуктов — оставьте поле пустым, поиск пойдёт по названию."
          >
            <Input
              value={cas}
              placeholder="например, 50-78-2"
              onChange={(event) => setCas(event.target.value)}
            />
          </Field>
        </div>

        {copiedFrom && (
          <p className="note">
            Поля заполнены из запроса №{copiedFrom.id} · {copiedFrom.name}.
            Проверьте объём и условия — они скопированы как были.
          </p>
        )}

        {nameIsCas && (
          <p className="note">
            В названии стоит CAS-номер.{" "}
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                setCas(normalizeCas(name));
                setName("");
              }}
            >
              Перенести его в поле номера
            </button>{" "}
            и написать название словами — по одному номеру поиск находит меньше.
          </p>
        )}

        {casEntered !== "" && !casValid && (
          <p className="error">
            {casSuggestion
              ? `В номере ошибка. Похоже, имелся в виду ${casSuggestion}`
              : "Номер не прошёл проверку формата и контрольной суммы."}
          </p>
        )}

        <div className="field">
          <label>
            <input
              type="checkbox"
              checked={analogAccepted}
              onChange={() => setAnalogAccepted((current) => !current)}
            />{" "}
            Подойдёт аналог — не обязательно ровно это вещество
          </label>
          {analogAccepted ? (
            <>
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
                Отметьте, чем аналог может отличаться. Без этих границ «аналог»
                означает для поставщика сразу всё перечисленное, и в ответ
                приходит не то, что просили.
              </span>
            </>
          ) : (
            <span className="note">
              Поиск пойдёт строго по названному веществу.
            </span>
          )}
        </div>

        <NameCandidates
          label="Другие названия того же вещества"
          hint="Равнозначные названия расширяют поиск. Без номера они служат основным якорем, но и с номером помогают: у карбомера запрос по номеру не нашёл ни одного поставщика, а по марке — сразу всех."
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

        <Field
          label="Требования к веществу"
          hint={
            analogAccepted
              ? "Что аналог обязан повторить: состав, INCI, активное содержание, ключевые показатели."
              : "То, по чему поставщик поймёт, что именно нужно: назначение, ключевые показатели, стандарт."
          }
        >
          <Textarea
            maxLength={4000}
            placeholder="Например: неионогенный загуститель для шампуня, вязкость 4000–6000 сП, pH 5–7"
            value={specification}
            onChange={(event) => setSpecification(event.target.value)}
          />
        </Field>

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

      {suggestions.length > 0 && (
        <aside className="panel similar-requests">
          <h3>Уже закупали похожее</h3>
          <p className="note">
            Нажмите на карточку — её параметры перенесутся в форму, и их можно
            будет поправить.
          </p>
          {suggestions.map((item) => (
            <button
              key={item.id}
              type="button"
              className="similar-card"
              disabled={copyBusy}
              onClick={() => void copyFromRequest(item)}
            >
              <span className="similar-card-name">{item.name}</span>
              <span className="similar-card-meta">
                №{item.id} · {item.cas ? `CAS ${item.cas}` : "без номера"}
              </span>
              <span className="similar-card-meta">
                {new Date(item.created_at).toLocaleDateString("ru-RU")} ·{" "}
                {STATUS_LABELS[item.status]}
                {item.n_quotations > 0
                  ? ` · котировок: ${item.n_quotations}`
                  : ""}
              </span>
            </button>
          ))}
        </aside>
      )}
    </div>
  );
}
