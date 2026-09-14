/** Public, authored examples only. Never populate this module from production data. */
export interface DemoSupplier {
  name: string;
  country: string;
  role: string;
  evidence: string;
  risk: string;
  price: number | null;
  moq: string;
  lead: string;
  payment: string;
}

export interface DemoRequest {
  id: string;
  name: string;
  english: string;
  cas: string;
  quantity: number;
  grade: string;
  status: string;
  purpose: string;
  specification: string;
  suppliers: DemoSupplier[];
}

export const demoRequests: DemoRequest[] = [
  {
    id: "DEMO-001", name: "Ацетилсалициловая кислота", english: "Acetylsalicylic acid",
    cas: "50-78-2", quantity: 500, grade: "Фармацевтический", status: "Сравнение предложений",
    purpose: "Пример полного цикла: от запроса сырья до сопоставления ответов трёх поставщиков.",
    specification: "Чистота ≥ 99,5%; упаковка 25 кг; требуются CoA на партию и TDS. Образец — до согласования закупки.",
    suppliers: [
      { name: "Demo Jiangsu Ingredients", country: "Китай", role: "Производитель (учебный пример)", evidence: "Acetylsalicylic acid, CAS 50-78-2. Manufactured at our facility. Assay ≥99.5%.", risk: "Требуется проверить CoA конкретной партии и производственную площадку.", price: 4.8, moq: "100 кг", lead: "30 дней", payment: "30% аванс, 70% перед отгрузкой" },
      { name: "Demo Gujarat Pharma", country: "Индия", role: "Производитель (учебный пример)", evidence: "Manufacturer of acetylsalicylic acid (50-78-2), pharmaceutical grade, 25 kg drums.", risk: "Не подтверждена доступность образца.", price: 5.2, moq: "250 кг", lead: "25 дней", payment: "50% аванс, 50% перед отгрузкой" },
      { name: "Demo Global Trading", country: "Китай", role: "Дистрибьютор (учебный пример)", evidence: "We distribute acetylsalicylic acid from partner factories.", risk: "Посредник; производитель партии не назван. Не входит в короткий список производителей.", price: 4.6, moq: "500 кг", lead: "Не указан", payment: "100% аванс" },
    ],
  },
  {
    id: "DEMO-002", name: "Лимонная кислота", english: "Citric acid, anhydrous",
    cas: "77-92-9", quantity: 2000, grade: "Пищевой", status: "Уточнение условий",
    purpose: "Пример неполного ответа: цена получена, но срок поставки ещё требует уточнения.",
    specification: "Безводная; чистота ≥ 99,5%; мешки по 25 кг. Нужны CoA и подтверждение пищевого грейда.",
    suppliers: [
      { name: "Demo Shandong Food Ingredients", country: "Китай", role: "Производитель (учебный пример)", evidence: "We manufacture anhydrous citric acid, CAS 77-92-9, food grade, 25 kg bags.", risk: "Срок поставки не указан. Подготовлен дозапрос; предложение пока неполное.", price: 1.15, moq: "1000 кг", lead: "Не указан", payment: "30% аванс, 70% перед отгрузкой" },
      { name: "Demo Western Ingredients", country: "Индия", role: "Роль не подтверждена", evidence: "Citric acid available. Please contact us for a quotation.", risk: "Нет CAS, подтверждения безводной формы и роли производителя. Нужна ручная проверка.", price: null, moq: "Не указан", lead: "Не указан", payment: "Не указана" },
    ],
  },
  {
    id: "DEMO-003", name: "Глицерин", english: "Glycerol",
    cas: "56-81-5", quantity: 1000, grade: "Косметический", status: "Проверка поставщиков",
    purpose: "Пример проверки идентичности и грейда: похожая карточка товара ещё не доказывает соответствие.",
    specification: "Чистота ≥ 99,5%; INCI: Glycerin; растительное происхождение. Нужны CoA, TDS и подтверждение происхождения.",
    suppliers: [
      { name: "Demo India Oleochemicals", country: "Индия", role: "Производитель (учебный пример)", evidence: "Vegetable glycerol, CAS 56-81-5, purity 99.5%. Produced at our oleochemical plant.", risk: "Косметический грейд и происхождение сырья требуют подтверждения документами.", price: null, moq: "250 кг", lead: "Не указан", payment: "Не указана" },
      { name: "Demo Eastern Chemicals", country: "Китай", role: "Роль не подтверждена", evidence: "Glycerin 95%, industrial grade.", risk: "Чистота и грейд не соответствуют запросу. Кандидат исключён из короткого списка.", price: null, moq: "Не указан", lead: "Не указан", payment: "Не указана" },
    ],
  },
];
