import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type RFQCreatePayload } from "../api/client";
import type {
  IdentificationMethod,
  ResolvedName,
  RFQListItem,
  RFQRead,
  SubstanceResolution,
} from "../api/types";
import NameCandidates from "./NameCandidates";
import { isValidCas, normalizeCas, suggestCheckDigit } from "./cas";
import {
  DEFAULT_SEARCH_MODE,
  SEARCH_MODES,
  modeCompanies,
  modeFromCompanies,
  type SearchModeKey,
} from "./searchModes";
import { STATUS_LABELS } from "./statusLabels";
import {
  Field,
  HelpTip,
  Icon,
  Input,
  Select,
  Textarea,
  type SelectOption,
} from "./ui";

// Базисы поставки. Набор и порядок обязаны совпадать с SUPPORTED_INCOTERMS
// в backend/app/services/incoterms.py — там же стоит тест, который ломается
// при расхождении. Здесь к коду добавлено русское пояснение: «FCA» и «DAP»
// сами по себе не говорят закупщику, до какого места довезёт поставщик.
//
// Пояснение описывает, кто что делает, и намеренно не обещает расчёт
// стоимости доставки: программа её не считает и считать не собирается.
const INCOTERM_OPTIONS: { code: string; hint: string }[] = [
  { code: "EXW", hint: "Забираем со склада продавца. Вывоз, экспорт и перевозка — на нас." },
  { code: "FCA", hint: "Продавец передаёт груз перевозчику и оформляет экспорт. Перевозка — на нас." },
  { code: "FOB", hint: "Продавец грузит на судно в своём порту. Фрахт и страховка — на нас." },
  { code: "CIP", hint: "Продавец везёт до названного места и страхует груз." },
  { code: "DAP", hint: "Продавец довозит до названного места. Ввозная растаможка — на нас." },
];

const COUNTRY_OPTIONS = ["Россия", "Китай", "Индия"];

// Грейд — величина справочная, вариантов конечное число. Значение хранится
// по-английски: оно уходит в письмо поставщику, а письмо английское.
// Списка грейдов в ТЗ нет: там названа чистота, а грейд упомянут в
// извлечении ответов и сравнении. Поэтому список открытый — «Другое»
// пропускает то, чего здесь не предусмотрели.
//
// Наверху — четыре отраслевых грейда, которыми закупщик пользуется чаще
// всего; промышленного среди них раньше не было вовсе, хотя основной
// объём закупки идёт именно по нему. Фармакопейные стандарты стояли
// первыми и вытесняли отраслевые вниз, поэтому убраны в свой раздел:
// USP и BP нужны узкому кругу запросов.
const GRADES: SelectOption[] = [
  { value: "", label: "Не задан" },
  { value: "Pharmaceutical grade", label: "Фармацевтический" },
  { value: "Industrial grade", label: "Промышленный" },
  { value: "Cosmetic grade", label: "Косметический" },
  { value: "Food grade", label: "Пищевой" },
  { value: "USP", label: "USP", group: "Фармакопейные стандарты" },
  { value: "EP (Ph. Eur.)", label: "EP / Ph. Eur.", group: "Фармакопейные стандарты" },
  { value: "BP", label: "BP", group: "Фармакопейные стандарты" },
  { value: "JP", label: "JP", group: "Фармакопейные стандарты" },
  { value: "ACS reagent", label: "ACS, реактивный", group: "Фармакопейные стандарты" },
  { value: "Feed grade", label: "Кормовой", group: "Прочие грейды" },
  { value: "Technical grade", label: "Технический", group: "Прочие грейды" },
  { value: "other", label: "Другое", group: "Прочие грейды" },
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
  // Форма открывается пустой. Демонстрационные «Ацетилсалициловая кислота»
  // и «50-78-2» стояли здесь с первых дней и читались как настоящее
  // содержимое запроса: их вычищали руками перед каждым вводом.
  const [cas, setCas] = useState("");
  const [specification, setSpecification] = useState("");
  const [synonyms, setSynonyms] = useState<string[]>([]);
  const [excludedNames, setExcludedNames] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [grade, setGrade] = useState("");
  const [gradeOther, setGradeOther] = useState("");
  const [purityPercent, setPurityPercent] = useState("");
  const [application, setApplication] = useState("");
  const [targetPrice, setTargetPrice] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [specialistComment, setSpecialistComment] = useState("");
  const [volumeAmount, setVolumeAmount] = useState("");
  const [volumeUnit, setVolumeUnit] = useState("kg");
  // По умолчанию отмечены не все базисы: письмо с пятью вариантами просит
  // поставщика посчитать пять цен, и он считает одну или не отвечает.
  // Три прежних варианта и были умолчанием — набор расширился, умолчание нет.
  const [incoterms, setIncoterms] = useState<string[]>(["CIP", "FCA", "EXW"]);
  const [countries, setCountries] = useState<string[]>(["Китай"]);
  const [searchMode, setSearchMode] = useState<SearchModeKey>(DEFAULT_SEARCH_MODE);
  const [pastRequests, setPastRequests] = useState<RFQListItem[]>([]);
  const [copiedFrom, setCopiedFrom] = useState<RFQListItem | null>(null);
  const [copyBusy, setCopyBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Опознание вещества по названию. Держим отдельно от создания запроса:
  // это разные действия, и провал одного не должен блокировать другое.
  const [resolution, setResolution] = useState<SubstanceResolution | null>(null);
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);
  // Вещество выбрано из результатов опознания. Название и номер после
  // этого закрыты на правку: они пришли из справочника вместе, и ручная
  // подмена одного из них рассогласует пару — в поиск уйдёт номер одного
  // вещества с названием другого. Замок снимается кнопкой.
  const [identityLocked, setIdentityLocked] = useState(false);
  // Названия, предложенные опознанием, но ещё не отмеченные человеком.
  // Отмечает он сам: равнозначное название и соседнее вещество различает
  // специалист, а не совпадение строк.
  const [suggestedSynonyms, setSuggestedSynonyms] = useState<string[]>([]);

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

  // Кнопка серая, пока нечего опознавать: у названия короче двух символов
  // выдача состоит из случайных совпадений.
  const nameForLookup = name.trim();
  const canResolve = nameForLookup.length >= 2 && !nameIsCas && !resolving;

  const runResolve = async () => {
    if (!canResolve) return;
    setResolving(true);
    setResolveError(null);
    try {
      const found = await api.resolveSubstance(nameForLookup);
      setResolution(found);
    } catch (err) {
      setResolveError(
        err instanceof ApiError ? err.message : "Не удалось опознать вещество",
      );
      setResolution(null);
    } finally {
      setResolving(false);
    }
  };

  // Выбранное название становится основным, а остальные равнозначные
  // предлагаются синонимами. Введённое человеком название тоже уходит в
  // предложения: поставщики нередко пишут именно так, как написал он.
  const applyCandidate = (candidate: ResolvedName) => {
    const others = (resolution?.candidates ?? [])
      .filter(
        (item) =>
          item.relation === "same" &&
          item.name.toLowerCase() !== candidate.name.toLowerCase(),
      )
      .map((item) => item.name);
    const merged = [...candidate.synonyms, ...others];
    if (
      nameForLookup &&
      nameForLookup.toLowerCase() !== candidate.name.toLowerCase()
    ) {
      merged.unshift(nameForLookup);
    }
    const unique: string[] = [];
    for (const item of merged) {
      if (!unique.some((value) => value.toLowerCase() === item.toLowerCase())) {
        unique.push(item);
      }
    }
    setName(candidate.name);
    // Номер подставляется только подтверждённый. Неподтверждённый показан
    // в карточке, но в поле не попадает: непроверенный номер хуже пустого.
    if (candidate.cas && candidate.cas_confirmed) setCas(candidate.cas);
    setSuggestedSynonyms(unique);
    setIdentityLocked(true);
  };

  const toggleExcluded = (candidateName: string) => {
    setExcludedNames((current) =>
      current.some((item) => item.toLowerCase() === candidateName.toLowerCase())
        ? current.filter(
            (item) => item.toLowerCase() !== candidateName.toLowerCase(),
          )
        : [...current, candidateName],
    );
  };

  const sameNames = (resolution?.candidates ?? []).filter(
    (item) => item.relation === "same",
  );
  const differentNames = (resolution?.candidates ?? []).filter(
    (item) => item.relation === "different",
  );

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
    supplier_target: modeCompanies(searchMode),
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
      setSearchMode(modeFromCompanies(source.supplier_target));
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
              disabled={identityLocked}
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
              disabled={identityLocked}
              placeholder="например, 50-78-2"
              onChange={(event) => setCas(event.target.value)}
            />
          </Field>
        </div>

        <div className="resolve-bar">
          {identityLocked ? (
            <>
              <span className="resolve-locked">
                <Icon name="lock" size={14} />
                Вещество выбрано из справочника
              </span>
              <button
                type="button"
                className="secondary"
                onClick={() => setIdentityLocked(false)}
              >
                Изменить
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="secondary"
                disabled={!canResolve}
                onClick={() => void runResolve()}
              >
                {resolving ? "Ищу вещество…" : "Проверить вещество"}
              </button>
              <HelpTip text="Ищет вещество по названию в справочнике PubChem и показывает найденные варианты: общепринятое написание, CAS-номер и равнозначные названия. В поля запроса встанет то, что вы выберете из списка." />
            </>
          )}
        </div>

        {resolveError && <p className="error">{resolveError}</p>}

        {resolution && (
          <div className="resolve-results">
            {sameNames.length > 0 && (
              <>
                <p className="resolve-heading">
                  Похоже, это одно и то же вещество. Нажмите на правильное
                  название — оно встанет в поля запроса.
                </p>
                {sameNames.map((item) => (
                  <button
                    key={`same-${item.name}`}
                    type="button"
                    className="resolve-card"
                    onClick={() => applyCandidate(item)}
                  >
                    <span className="resolve-card-head">
                      <span className="resolve-card-name">{item.name}</span>
                      <span
                        className={
                          item.cas_confirmed
                            ? "resolve-cas is-confirmed"
                            : "resolve-cas"
                        }
                      >
                        {item.cas
                          ? `CAS ${item.cas}`
                          : "номер не найден"}
                      </span>
                    </span>
                    {item.reason && (
                      <span className="resolve-card-reason">{item.reason}</span>
                    )}
                    {item.quote && (
                      <span className="resolve-card-quote">«{item.quote}»</span>
                    )}
                    <span className="resolve-card-source">
                      {item.source === "pubchem"
                        ? "Справочник PubChem"
                        : item.source_url || "веб-источник"}
                    </span>
                  </button>
                ))}
              </>
            )}

            {differentNames.length > 0 && (
              <>
                <p className="resolve-heading resolve-heading-warn">
                  Названия рядом, вещества разные. Нажмите, чтобы такое
                  название не попало в поиск.
                </p>
                {differentNames.map((item) => {
                  const marked = excludedNames.some(
                    (value) => value.toLowerCase() === item.name.toLowerCase(),
                  );
                  return (
                    <button
                      key={`diff-${item.name}`}
                      type="button"
                      className={
                        marked
                          ? "resolve-card resolve-card-warn is-marked"
                          : "resolve-card resolve-card-warn"
                      }
                      onClick={() => toggleExcluded(item.name)}
                    >
                      <span className="resolve-card-head">
                        <span className="resolve-card-name">{item.name}</span>
                        <span className="resolve-cas">
                          {item.cas ? `CAS ${item.cas}` : "номер не найден"}
                        </span>
                      </span>
                      {item.reason && (
                        <span className="resolve-card-reason">{item.reason}</span>
                      )}
                      <span className="resolve-card-source">
                        {marked ? "Исключено из поиска" : "Отметить как не то"}
                      </span>
                    </button>
                  );
                })}
              </>
            )}

            {resolution.warnings.map((warning) => (
              <p key={warning} className="note resolve-warning">
                {warning}
              </p>
            ))}
          </div>
        )}

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
            candidates={suggestedSynonyms}
            value={synonyms}
            onChange={setSynonyms}
          />
          <NameCandidates
            label="Похожие названия, которые НЕ подходят"
            hint="Соседние по названию вещества — другая соль, другой грейд. Они уйдут в отрицательный фильтр, иначе поиск найдёт настоящих поставщиков не того вещества."
            placeholder="например, Betaine hydrochloride"
            candidates={differentNames.map((item) => item.name)}
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

        {/* Не Field: тот оборачивает содержимое в label, а каждый режим —
            сам label со своей радиокнопкой, и вложенные label ломают
            разметку. */}
        <div className="field">
          <div className="heading-with-help">
            <label>Насколько тщательно искать</label>
            <HelpTip text="Режим задаёт, сколько компаний агент откроет и проверит в каждой стране. Это объём проверки, а не обещание результата: производителем оказывается не всякая проверенная компания, остальные — торговые дома, площадки и справочники. Число поисковых запросов режим не меняет." />
          </div>
          <div className="search-modes">
            {SEARCH_MODES.map((mode) => (
              <label
                key={mode.key}
                className={`search-mode${searchMode === mode.key ? " active" : ""}`}
              >
                <input
                  type="radio"
                  name="search-mode"
                  value={mode.key}
                  checked={searchMode === mode.key}
                  onChange={() => setSearchMode(mode.key)}
                />
                <span className="search-mode-label">{mode.label}</span>
                <span className="search-mode-hint">{mode.hint}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="field">
          <div className="heading-with-help">
            <label>Условия поставки</label>
            <HelpTip text="Базис поставки говорит, до какого места везёт поставщик и с какого места расходы и риск переходят к покупателю. Отмеченные базисы уходят в письмо, и поставщик называет цену по каждому. Стоимость доставки программа не рассчитывает — её называет поставщик." />
          </div>
          <div className="incoterms">
            {INCOTERM_OPTIONS.map(({ code, hint }) => (
              <label
                key={code}
                className={`incoterm${incoterms.includes(code) ? " active" : ""}`}
              >
                <span className="incoterm-head">
                  <input
                    type="checkbox"
                    checked={incoterms.includes(code)}
                    onChange={() => toggleIncoterm(code)}
                  />
                  <span className="incoterm-code">{code}</span>
                </span>
                <span className="incoterm-hint">{hint}</span>
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
