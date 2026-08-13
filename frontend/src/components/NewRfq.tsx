import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type RFQCreatePayload } from "../api/client";
import type {
  IdentificationMethod,
  RFQListItem,
  RFQRead,
} from "../api/types";
import NameCandidates from "./NameCandidates";
import { isValidCas, normalizeCas, suggestCheckDigit } from "./cas";
import { STATUS_LABELS } from "./statusLabels";
import { Field, Input, Select, Textarea } from "./ui";

const ALL_INCOTERMS = ["CIP", "FCA", "EXW"];
const COUNTRY_OPTIONS = ["Россия", "Китай", "Индия"];

// Грейд — величина справочная, вариантов конечное число. Значение хранится
// по-английски: оно уходит в письмо поставщику, а письмо английское.
// Списка грейдов в ТЗ нет: там названа чистота, а грейд упомянут в
// извлечении ответов и сравнении. Поэтому список открытый — «Другое»
// пропускает то, чего здесь не предусмотрели.
const GRADES: { value: string; label: string }[] = [
  { value: "", label: "Не задан" },
  { value: "USP", label: "USP" },
  { value: "EP (Ph. Eur.)", label: "EP / Ph. Eur." },
  { value: "BP", label: "BP" },
  { value: "JP", label: "JP" },
  { value: "ACS reagent", label: "ACS, реактивный" },
  { value: "Pharmaceutical grade", label: "Фармацевтический" },
  { value: "Food grade", label: "Пищевой" },
  { value: "Cosmetic grade", label: "Косметический" },
  { value: "Feed grade", label: "Кормовой" },
  { value: "Technical grade", label: "Технический" },
  { value: "other", label: "Другое" },
];

const CURRENCIES: { value: string; label: string }[] = [
  { value: "USD", label: "USD" },
  { value: "EUR", label: "EUR" },
  { value: "CNY", label: "CNY" },
  { value: "RUB", label: "RUB" },
];

// Единица тоже уходит в письмо, поэтому подпись русская, а значение — нет.
const VOLUME_UNITS: { value: string; label: string }[] = [
  { value: "g", label: "г" },
  { value: "kg", label: "кг" },
  { value: "t", label: "т" },
  { value: "L", label: "л" },
  { value: "mL", label: "мл" },
];

// Разбор сохранённых строк: карточка прошлого запроса хранит «USP, min 99%»
// и «500 kg» одной строкой, а форма показывает их раздельными полями.
const parsePurity = (
  stored: string | null,
): { percent: string; grade: string; gradeOther: string } => {
  const text = (stored || "").trim();
  if (!text) return { percent: "", grade: "", gradeOther: "" };
  const number = /(\d+(?:[.,]\d+)?)\s*%/.exec(text);
  const percent = number ? number[1].replace(",", ".") : "";
  // Остаток строки после числа — это и есть грейд, известный или чужой.
  const rest = text
    .replace(/min\s*\d+(?:[.,]\d+)?\s*%/i, "")
    .replace(/\d+(?:[.,]\d+)?\s*%/, "")
    .replace(/^[\s,;]+|[\s,;]+$/g, "");
  const known = GRADES.map((item) => item.value)
    .filter((value) => value && value !== "other")
    .find((value) => rest.toLowerCase() === value.toLowerCase());
  if (known) return { percent, grade: known, gradeOther: "" };
  if (rest) return { percent, grade: "other", gradeOther: rest };
  return { percent, grade: "", gradeOther: "" };
};

const parseVolume = (stored: string | null): [string, string] => {
  const match = /^\s*(\d+(?:[.,]\d+)?)\s*(.*)$/.exec(stored || "");
  if (!match) return ["", "kg"];
  const tail = match[2].trim().toLowerCase();
  const unit =
    VOLUME_UNITS.find(
      (item) =>
        item.value.toLowerCase() === tail || item.label.toLowerCase() === tail,
    )?.value || "kg";
  return [match[1].replace(",", "."), unit];
};

interface Props {
  onCreated: (rfq: RFQRead) => void;
}

export default function NewRfq({ onCreated }: Props) {
  const [cas, setCas] = useState("50-78-2");
  const [specification, setSpecification] = useState("");
  const [synonyms, setSynonyms] = useState<string[]>([]);
  const [excludedNames, setExcludedNames] = useState<string[]>([]);
  const [name, setName] = useState("Ацетилсалициловая кислота");
  const [grade, setGrade] = useState("");
  const [gradeOther, setGradeOther] = useState("");
  const [purityPercent, setPurityPercent] = useState("");
  const [application, setApplication] = useState("");
  const [targetPrice, setTargetPrice] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [specialistComment, setSpecialistComment] = useState("");
  const [volumeAmount, setVolumeAmount] = useState("500");
  const [volumeUnit, setVolumeUnit] = useState("kg");
  const [incoterms, setIncoterms] = useState<string[]>(["CIP", "FCA", "EXW"]);
  const [countries, setCountries] = useState<string[]>(["Китай"]);
  const [supplierTarget, setSupplierTarget] = useState(5);
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
  const method: IdentificationMethod = casValid ? "cas" : "spec";

  const payload = (): RFQCreatePayload => ({
    identification_method: method,
    // Непрошедший проверку номер не уходит в запрос: форма к этому моменту
    // уже не даёт создать запрос с таким полем.
    cas: casValid ? casNormalized : null,
    // Запрос создаётся строго по названному веществу: поиск аналога из формы
    // убран, поле остаётся в контракте ради уже созданных запросов.
    analog_reference: null,
    analog_variations: [],
    // Скрытое поле не отправляется: иначе набранные до ввода номера
    // требования молча уехали бы в письмо поставщику.
    specification: casValid ? null : specification.trim() || null,
    confirmed_synonyms: synonyms,
    excluded_names: excludedNames,
    name: name.trim(),
    incoterms,
    // Чистота и грейд лежат в базе одной строкой: число впереди, потому
    // что ТЗ называет именно чистоту, а грейд идёт уточнением.
    purity:
      [
        purityPercent.trim() ? `min ${purityPercent.trim()}%` : "",
        grade === "other" ? gradeOther.trim() : grade,
      ]
        .filter(Boolean)
        .join(", ") || null,
    application: application.trim() || null,
    volume: volumeAmount.trim()
      ? `${volumeAmount.trim()} ${volumeUnit}`
      : null,
    channels: ["email"],
    search_countries: countries,
    supplier_target: supplierTarget,
    additional_instructions: null,
    target_price: targetPrice.trim() ? Number(targetPrice) : null,
    currency,
    specialist_comment: specialistComment.trim() || null,
    // Карточку справочника форма больше не привязывает: связь ставится
    // при подтверждении идентичности в самом поиске.
    substance_id: null,
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
      const source = await api.getRfq(item.id);
      setName(source.name);
      setCas(source.cas || "");
      setSpecification(source.specification || "");
      setSynonyms(source.confirmed_synonyms || []);
      setExcludedNames(source.excluded_names || []);
      const purity = parsePurity(source.purity);
      setPurityPercent(purity.percent);
      setGrade(purity.grade);
      setGradeOther(purity.gradeOther);
      setApplication(source.application || "");
      setTargetPrice(source.target_price != null ? String(source.target_price) : "");
      setCurrency(source.currency || "USD");
      setSpecialistComment(source.specialist_comment || "");
      const [sourceAmount, sourceUnit] = parseVolume(source.volume);
      setVolumeAmount(sourceAmount);
      setVolumeUnit(sourceUnit);
      if (source.incoterms?.length) setIncoterms(source.incoterms);
      if (source.search_countries?.length) setCountries(source.search_countries);
      setSupplierTarget(source.supplier_target);
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
  //
  // Список причин собирается здесь же, а не выводится отдельной проверкой:
  // неактивная кнопка без объяснения оставляет закупщика гадать, чего от
  // него хотят, а поля, которых не хватает, могут быть ниже экрана.
  const blockers: string[] = [];
  if (!name.trim()) {
    blockers.push("укажите, что нужно закупить");
  }
  if (casEntered && !casValid) {
    blockers.push("исправьте CAS-номер или очистите поле");
  }
  if (countries.length === 0) {
    blockers.push("выберите хотя бы одну страну поиска");
  }
  if (incoterms.length === 0) {
    blockers.push("отметьте хотя бы одно условие поставки");
  }

  const canCreate = !busy && blockers.length === 0;

  return (
    <div className={`new-rfq${suggestions.length > 0 ? " has-suggestions" : ""}`}>
      <div className="panel">
        <h2>Создать новый запрос</h2>

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

        <div className="row">
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
        </div>

        {/* Требования участвуют в построении поисковых запросов только
            без номера — там они второй якорь наравне с названием. При
            известном номере вещество определено однозначно, и роль этого
            поля выполняет «Чистота / грейд». */}
        {!casValid && (
          <Field
            label="Требования к веществу"
            hint="Номера нет — значит искать будут по этому описанию."
          >
            <Textarea
              maxLength={4000}
              placeholder="Например: неионогенный загуститель для шампуня, вязкость 4000–6000 сП, pH 5–7"
              value={specification}
              onChange={(event) => setSpecification(event.target.value)}
            />
          </Field>
        )}

        <div className="row row-compact">
          {/* ТЗ называет на входе чистоту, поэтому она первая и числом:
              закупщик проверяет её именно так — «не ниже 99». Грейд идёт
              уточнением и остаётся необязательным. */}
          <Field className="field-narrow" label="Чистота не ниже, %">
            <Input
              type="number"
              min={0}
              max={100}
              step="0.1"
              placeholder="99"
              value={purityPercent}
              onChange={(event) => setPurityPercent(event.target.value)}
            />
          </Field>
          <Field className="field-grade" label="Грейд / стандарт">
            <Select value={grade} options={GRADES} onChange={setGrade} />
          </Field>
          {grade === "other" && (
            <Field className="field-grade" label="Какой именно">
              <Input
                placeholder="например, Ph. Eur. + FCC"
                value={gradeOther}
                onChange={(event) => setGradeOther(event.target.value)}
              />
            </Field>
          )}
          {/* Число и единица — одно значение, поэтому и поле одно: иначе
              единица отрывается от своего числа при переносе строки. */}
          <Field className="field-volume" label="Требуемый объём">
            <div className="volume-input">
              <Input
                type="number"
                min={0}
                step="any"
                placeholder="500"
                value={volumeAmount}
                onChange={(event) => setVolumeAmount(event.target.value)}
              />
              <Select
                className="volume-unit"
                ariaLabel="Единица измерения"
                value={volumeUnit}
                options={VOLUME_UNITS}
                onChange={setVolumeUnit}
              />
            </div>
          </Field>
          <Field className="field-narrow" label="Ориентир цены">
            <Input
              type="number"
              min={0}
              step="any"
              placeholder="не обязательно"
              value={targetPrice}
              onChange={(event) => setTargetPrice(event.target.value)}
            />
          </Field>
          <Field className="field-unit" label="Валюта">
            <Select
              value={currency}
              options={CURRENCIES}
              onChange={setCurrency}
            />
          </Field>
        </div>

        <Field
          label="Область применения"
          hint="Для чего вещество нужно. Уходит в письмо поставщику: по применению он подскажет подходящий грейд."
        >
          <Textarea
            maxLength={1000}
            placeholder="Например: эмульгатор для битумной эмульсии"
            value={application}
            onChange={(event) => setApplication(event.target.value)}
          />
        </Field>

        <Field
          label="Комментарий специалиста"
          hint="Внутренняя заметка по позиции. Поставщику не отправляется."
          className="comment-field"
        >
          <Textarea
            maxLength={4000}
            placeholder="Например: в прошлый раз брали у казанского завода, качество устроило"
            value={specialistComment}
            onChange={(event) => setSpecialistComment(event.target.value)}
          />
        </Field>

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
          <button
            onClick={() => void onCreate()}
            disabled={!canCreate}
            title={
              blockers.length > 0
                ? `Чтобы начать поиск: ${blockers.join("; ")}`
                : undefined
            }
          >
            {busy ? "Создаём и ставим поиск в очередь…" : "Создать запрос и начать поиск"}
          </button>
        </div>

        {blockers.length === 1 && (
          <p className="note blockers-note">Чтобы начать поиск, {blockers[0]}.</p>
        )}
        {blockers.length > 1 && (
          <div className="note blockers-note">
            Чтобы начать поиск:
            <ul>
              {blockers.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}

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
