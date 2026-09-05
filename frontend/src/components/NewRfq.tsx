import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type RFQCreatePayload } from "../api/client";
import type {
  AnalogVariation,
  IdentificationMethod,
  ResolvedName,
  RFQListItem,
  RFQRead,
  SubstanceResolution,
} from "../api/types";
import IncotermPicker from "./IncotermPicker";
import NameCandidates from "./NameCandidates";
import RfqImport from "./RfqImport";
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

const COUNTRY_OPTIONS = ["Россия", "Китай", "Индия"];

// Чем аналог может отличаться от эталона. Слово «аналог» само по себе
// означает сразу всё перечисленное, и поставщик отвечает не тем, что
// ждали: границы замены задаёт закупщик, а не догадка модели.
// Значения совпадают с AnalogVariation в контракте backend.
const ANALOG_VARIATIONS: { value: AnalogVariation; label: string; hint: string }[] = [
  {
    value: "salt",
    label: "Другая соль или форма",
    hint: "Подойдёт гидрохлорид, гидрат или эфир вместо основания.",
  },
  {
    value: "purity",
    label: "Другая чистота или грейд",
    hint: "Подойдёт другой грейд, если остальные требования выполнены.",
  },
  {
    value: "form",
    label: "Другое физическое состояние",
    hint: "Подойдёт порошок вместо гранул или раствора.",
  },
  {
    value: "manufacturer",
    label: "Другой производитель",
    hint: "Подойдёт то же вещество любого завода, не только эталонного.",
  },
];

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

// Названия сравниваются без учёта регистра и лишних пробелов: «Betaine»,
// «betaine » и «BETAINE» — одно название, и в списке им место одно.
const nameKey = (value: string) => value.trim().toLocaleLowerCase();

const dedupeNames = (names: string[]): string[] => {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of names) {
    const name = value.trim();
    const key = nameKey(name);
    if (!name || seen.has(key)) continue;
    seen.add(key);
    result.push(name);
  }
  return result;
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
  /** Пакет создан — открыть его сводку. */
  onBatchCreated: (batchId: number) => void;
  /** Предзаполнение при переходе с наводки: вещество из прошлого запроса. */
  initialName?: string;
  initialCas?: string;
  /**
   * Изготовитель из чужого паспорта — тот, кого стоит поискать.
   * Подтверждённым поставщиком он не является: известно только имя из
   * документа, который прислал кто-то другой.
   */
  initialManufacturer?: string;
}

export default function NewRfq({
  onCreated,
  onBatchCreated,
  initialName = "",
  initialCas = "",
  initialManufacturer = "",
}: Props) {
  // Форма открывается пустой. Демонстрационные «Ацетилсалициловая кислота»
  // и «50-78-2» стояли здесь с первых дней и читались как настоящее
  // содержимое запроса: их вычищали руками перед каждым вводом.
  const [cas, setCas] = useState(initialCas);
  const [specification, setSpecification] = useState("");
  const [synonyms, setSynonyms] = useState<string[]>([]);
  const [excludedNames, setExcludedNames] = useState<string[]>([]);
  const [name, setName] = useState(initialName);
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
  // Поиск аналога выключен по умолчанию и включается явно. Молча искать
  // замену нельзя: закупщик, который просил конкретный продукт, получил бы
  // похожий и узнал об этом только из ответа поставщика.
  const [analogMode, setAnalogMode] = useState(false);
  const [analogReference, setAnalogReference] = useState("");
  const [analogVariations, setAnalogVariations] = useState<AnalogVariation[]>([]);
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
  // Наводка на изготовителя. Видна и снимается: она уходит в поиск как
  // указание агенту, и скрытое указание в письме — худший вид сюрприза.
  const [manufacturerLead, setManufacturerLead] = useState(initialManufacturer);
  const [suggestedSynonyms, setSuggestedSynonyms] = useState<string[]>([]);
  // Названия, которые закупщик снял руками. Автозаполнение обязано их
  // помнить: иначе повторный выбор той же карточки молча вернёт снятое,
  // и решение человека отменится перерисовкой формы.
  const [dismissedSynonyms, setDismissedSynonyms] = useState<string[]>([]);

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
  const isDismissed = (name: string) =>
    dismissedSynonyms.some((item) => nameKey(item) === nameKey(name));

  // Снятие и возврат названия — решения закупщика, и оба надо запомнить:
  // снятое не должно возвращаться автозаполнением, возвращённое не должно
  // считаться снятым.
  const changeSynonyms = (next: string[]) => {
    const removed = synonyms.filter(
      (item) => !next.some((value) => nameKey(value) === nameKey(item)),
    );
    const added = next.filter(
      (item) => !synonyms.some((value) => nameKey(value) === nameKey(item)),
    );
    if (removed.length || added.length) {
      setDismissedSynonyms((current) =>
        dedupeNames([
          ...current.filter(
            (item) => !added.some((value) => nameKey(value) === nameKey(item)),
          ),
          ...removed,
        ]),
      );
    }
    setSynonyms(next);
  };

  const applyCandidate = (candidate: ResolvedName) => {
    const others = (resolution?.candidates ?? []).filter(
      (item) =>
        item.relation === "same" &&
        item.name.toLowerCase() !== candidate.name.toLowerCase(),
    );

    // Автозаполняется только то, что подтвердил справочник. Синонимы
    // PubChem — запись реестра: там уже отброшены номера и складские
    // артикулы, и название относится к той самой карточке. Веб-кандидат —
    // прочтение страницы моделью: связь «то же вещество» там утверждение,
    // а не факт, и в рабочий список оно попадает только рукой закупщика.
    // Неверный синоним в поиске опаснее пустого поля: он находит настоящих
    // поставщиков не того вещества, и провал выглядит как успех.
    const confirmed =
      candidate.source === "pubchem" ? [...candidate.synonyms] : [];
    confirmed.push(
      ...others.filter((item) => item.source === "pubchem").map((item) => item.name),
    );

    // Показываются все равнозначные названия, включая веб: закупщик видит
    // их рядом с автодобавленными и отмечает нужные одним кликом. Сужен
    // именно набор автоотметки, а не список предложений — иначе синонимы
    // веб-карточки пропали бы из формы совсем.
    const shown = [
      ...confirmed,
      ...candidate.synonyms,
      ...others.map((item) => item.name),
    ];
    if (
      nameForLookup &&
      nameForLookup.toLowerCase() !== candidate.name.toLowerCase()
    ) {
      // Собственное написание закупщика — не подтверждённое название, а
      // его формулировка. Предлагаем, но сами не отмечаем.
      shown.unshift(nameForLookup);
    }

    setName(candidate.name);
    // Номер подставляется только подтверждённый. Неподтверждённый показан
    // в карточке, но в поле не попадает: непроверенный номер хуже пустого.
    if (candidate.cas && candidate.cas_confirmed) setCas(candidate.cas);
    // Название, совпавшее с основным, синонимом не является. Справочник
    // его и так отсеивает, но отметка уходит в письмо поставщику, и
    // полагаться здесь на чужую аккуратность не стоит.
    const isMainName = (item: string) => nameKey(item) === nameKey(candidate.name);

    setSuggestedSynonyms(dedupeNames(shown.filter((item) => !isMainName(item))));
    setSynonyms((current) =>
      dedupeNames([
        ...current,
        // Снятое руками не возвращается: повторный выбор той же карточки
        // не должен отменять решение закупщика.
        ...confirmed.filter((item) => !isMainName(item) && !isDismissed(item)),
      ]),
    );
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

  // Откуда взялось название и почему. Ищется и среди самих кандидатов, и
  // среди синонимов карточки: в поле синонимов попадает и то, и другое.
  const explainName = (value: string): string | undefined => {
    const key = nameKey(value);
    const candidate = (resolution?.candidates ?? []).find(
      (item) => nameKey(item.name) === key,
    );
    if (candidate) {
      const origin =
        candidate.source === "pubchem"
          ? "Источник: справочник PubChem."
          : "Источник: страница из поиска, прочитанная ИИ-агентом. Связь с веществом — его вывод, а не запись реестра, поэтому название не отмечено автоматически.";
      return `${origin} ${candidate.reason}`.trim();
    }
    const owner = (resolution?.candidates ?? []).find((item) =>
      item.synonyms.some((synonym) => nameKey(synonym) === key),
    );
    if (owner) {
      return owner.source === "pubchem"
        ? `Источник: справочник PubChem, синоним карточки «${owner.name}». Отмечено автоматически как равнозначное название.`
        : `Источник: страница из поиска, синоним карточки «${owner.name}». Прочтение страницы ИИ-агентом, а не запись реестра, поэтому название не отмечено автоматически.`;
    }
    // Именно resolution.query, а не текущее содержимое поля: выбор карточки
    // подменяет название в поле, и живая переменная перестала бы совпадать
    // с тем написанием, которое закупщик когда-то ввёл.
    if (resolution?.query && nameKey(resolution.query) === key) {
      return "Ваше написание названия. Поставщики нередко пишут именно так, поэтому оно предложено — но справочником оно не подтверждено и само не отмечается.";
    }
    return undefined;
  };

  // Эталон по умолчанию — то, что закупщик уже назвал в запросе: чаще
  // всего он и есть образец, замену которому ищут. Отдельное поле нужно
  // для случая, когда эталон — торговая марка, а закупают по функции.
  const analogReferenceValue = analogReference.trim() || name.trim();

  // Способ идентификации остаётся в контракте поиска — он строит разные
  // запросы. Точный и спецификационный выводятся из заполненного, а поиск
  // аналога человек включает сам: это другая задача, а не другое поле.
  const method: IdentificationMethod = analogMode
    ? "analog"
    : casValid
      ? "cas"
      : "spec";

  const payload = (): RFQCreatePayload => ({
    identification_method: method,
    // Непрошедший проверку номер не уходит в запрос: форма к этому моменту
    // уже не даёт создать запрос с таким полем.
    cas: casValid ? casNormalized : null,
    analog_reference: analogMode ? analogReferenceValue : null,
    analog_variations: analogMode ? analogVariations : [],
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
    additional_instructions: manufacturerLead.trim()
      ? `Проверь в первую очередь изготовителя «${manufacturerLead.trim()}»: ` +
        "его название стоит в паспорте качества, полученном от другого " +
        "поставщика. Компания не подтверждена — это наводка."
      : null,
    target_price: targetPrice.trim() ? Number(targetPrice) : null,
    currency,
    specialist_comment: specialistComment.trim() || null,
    // Карточку справочника форма больше не привязывает: связь ставится
    // при подтверждении идентичности в самом поиске.
    substance_id: null,
  });

  const toggleAnalogVariation = (variation: AnalogVariation) =>
    setAnalogVariations((current) =>
      current.includes(variation)
        ? current.filter((item) => item !== variation)
        : [...current, variation],
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
      // Повтор запроса на аналог остаётся запросом на аналог: скопировать
      // условия и молча сменить задачу на точный поиск нельзя.
      const sourceIsAnalog = source.identification_method === "analog";
      setAnalogMode(sourceIsAnalog);
      setAnalogReference(sourceIsAnalog ? source.analog_reference || "" : "");
      setAnalogVariations(
        sourceIsAnalog
          ? ANALOG_VARIATIONS.map((item) => item.value).filter((value) =>
              (source.analog_variations || []).includes(value),
            )
          : [],
      );
      setSynonyms(source.confirmed_synonyms || []);
      // Форма перезаполняется чужим запросом целиком, поэтому память о
      // снятых названиях сбрасывается: она относилась к прошлому набору.
      setDismissedSynonyms([]);
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

        {/* Список из файла — вход для закупки на несколько позиций. Форма
            ниже остаётся входом для одной: это разные задачи, и подменять
            одну другой нельзя. Пакетное создание запросов по разобранным
            строкам — отдельная задача (MEET2-02); пока экран показывает,
            что именно система прочитала в файле. */}
        {/* Наводка из чужого паспорта. Показана и снимается: она уходит
            в поиск указанием агенту, а скрытое указание — худший вид
            сюрприза. Подтверждённым поставщиком компания не является. */}
        {manufacturerLead && (
          <div className="manufacturer-lead">
            <div>
              <strong>Ищем изготовителя: {manufacturerLead}</strong>
              <p className="note">
                Название взято из паспорта качества, который прислал другой
                поставщик. Компания не подтверждена — поиск получит её как
                наводку, а не как готовый ответ.
              </p>
            </div>
            <button
              className="link-btn"
              type="button"
              onClick={() => setManufacturerLead("")}
            >
              убрать
            </button>
          </div>
        )}
        <RfqImport onCreated={onBatchCreated} />

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
            onChange={changeSynonyms}
            hintFor={explainName}
          />
          <NameCandidates
            label="Похожие названия, которые НЕ подходят"
            hint="Соседние по названию вещества — другая соль, другой грейд. Они уйдут в отрицательный фильтр, иначе поиск найдёт настоящих поставщиков не того вещества."
            placeholder="например, Betaine hydrochloride"
            candidates={differentNames.map((item) => item.name)}
            value={excludedNames}
            onChange={setExcludedNames}
            hintFor={explainName}
          />
        </div>

        {/* Поиск аналога — отдельная задача, а не послабление точного
            поиска: он ищет замену эталону, и совпадение по функции здесь
            не является совпадением по веществу. Поэтому переключатель, а
            не молчаливое расширение выдачи. */}
        <div className="field analog-block">
          <label className="analog-switch">
            <span className="analog-switch-head">
              <input
                type="checkbox"
                aria-label="Искать возможный аналог"
                checked={analogMode}
                onChange={(event) => setAnalogMode(event.target.checked)}
              />
              <span className="analog-switch-label">Искать возможный аналог</span>
            </span>
            <span className="analog-switch-hint">
              Обычный поиск ищет названное вещество. Аналог — это другой
              продукт со схожей функцией: он никогда не считается точным
              совпадением и всегда уходит на проверку специалисту.
            </span>
          </label>

          {analogMode && (
            <div className="analog-details">
              <Field
                label="Эталон: на что должен быть похож аналог"
                hint="Продукт или торговая марка, замену которой ищем. Если оставить пустым, эталоном станет название из запроса."
              >
                <Input
                  maxLength={255}
                  placeholder={name.trim() || "например, Dowsil 556"}
                  value={analogReference}
                  onChange={(event) => setAnalogReference(event.target.value)}
                />
              </Field>

              <div className="field">
                <div className="heading-with-help">
                  <label>Что можно менять относительно эталона</label>
                  <HelpTip text="Без этих границ «аналог» для поставщика означает что угодно, и в ответ приходит не то, что просили. Отмеченное уходит в письмо отдельными строками." />
                </div>
                <div className="analog-variations">
                  {ANALOG_VARIATIONS.map(({ value, label, hint }) => (
                    <label
                      key={value}
                      className={`analog-variation${
                        analogVariations.includes(value) ? " active" : ""
                      }`}
                    >
                      <span className="analog-variation-head">
                        <input
                          type="checkbox"
                          checked={analogVariations.includes(value)}
                          onChange={() => toggleAnalogVariation(value)}
                        />
                        <span className="analog-variation-label">{label}</span>
                      </span>
                      <span className="analog-variation-hint">{hint}</span>
                    </label>
                  ))}
                </div>
                {analogVariations.length === 0 && (
                  <p className="analog-note">
                    Границы замены не заданы — поставщик решит их сам, и
                    предложение может не подойти. Отметьте хотя бы одну.
                  </p>
                )}
              </div>
            </div>
          )}
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
            <HelpTip text="Базис поставки говорит, до какого места везёт поставщик и с какого места расходы и риск переходят к покупателю. Наведите на код в списке — увидите, кто что делает. Выбранные базисы уходят в письмо, и поставщик называет цену по каждому. Условие, которого нет в списке, впишите прямо в поле: оно уйдёт как есть, а место поставки поставщик подтвердит в ответе. Стоимость доставки программа не рассчитывает — её называет поставщик." />
          </div>
          <IncotermPicker values={incoterms} onChange={setIncoterms} />
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
