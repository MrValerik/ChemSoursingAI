# Исследование поиска сырья по списку заказчика — 2026-08-04

## Объём и методика

Источник: лист `Список сырья` файла `Список_сырья_цены_17.07.xlsx` из папки
официальных ресурсов заказчика. В анализ включены 323 позиции; коммерческие цены
в репозиторий не копировались.

Позиции разделены по тому, какой якорь реально может использовать поисковая
система:

| Сложность | Позиций | Доля | Критерий |
|---|---:|---:|---|
| Лёгкие | 145 | 44,9% | Валидный CAS и достаточно однозначное товарное имя |
| Средние | 7 | 2,2% | CAS есть, но имя содержит грейд, форму, смесь изомеров или неопределённость |
| Сложные | 171 | 52,9% | CAS отсутствует, нужен аналог либо выбор между чистым веществом и смесью |

CAS отсутствует у 170 из 323 строк (52,6%). Сложная группа раскладывается на
60 продуктов без CAS и без явного требования аналога, 108 запросов аналога и
3 запроса вида «найти чистое вещество или смесь на основе». Внутри 108 аналогов
99 наименований явно относятся к семейству DOWSIL / Dow Corning / XIAMETER:
80 XIAMETER, 18 DOWSIL и 1 Dow Corning. Ещё три позиции SYL-OFF также относятся
к торговой линейке Dow. Следовательно, основная сложность списка — не поиск
молекулы, а поиск функциональной замены фирменному промышленному продукту.

Категории пересчитаны после нормализации Unicode-дефисов. Поэтому лёгких
позиций 145, а не 143: `27458‑92‑0` и `112‑70‑9` содержат неразрывный дефис
U+2011, но являются валидными CAS после нормализации.

## Контрольные позиции

### Лёгкие

Проверены `1-Propanol` (71-23-8), `Adipic acid` (124-04-9) и
`4-Chlorophenol` (106-48-9). Для каждой позиции Serper вернул по 32 сырых
результата на четыре запроса, PubChem подтвердил CAS. После отсева каталогов и
переранжирования карточки компаний и продуктов поднялись выше нормативных PDF и
деклараций состава. Для адипиновой кислоты официальный результат Shandong Hualu
Hengsheng переместился примерно с 19-го места на 2-е.

Независимые первичные ориентиры:

- BASF, n-Propanol: https://chemicals.basf.com/global/en/Petrochemicals/alcohols-glycol_ethers-and-solvents/products/alcohols-and-aldehydes
- Shandong Hualu Hengsheng, Adipic Acid: https://www.hualu-hengsheng.com/products/new-materials/shandong-hualu-hengsheng-chemical-co-ltd-adipic-acid-cas-124-04-9.html
- Sinochem Nanjing, p-Chlorophenol: https://www.sinochem-nanjing.com/products/solvents/p-chlorophenol-cas-106-48-9.html

### Средние

В группе ровно семь позиций:

1. Carbomer (cross-linked polyacrylic acid), 9003-01-4;
2. Colloidal silicon dioxide (fumed silica; Aerosil grade), 7631-86-9;
3. Ethylene-vinyl acetate copolymer, 24937-78-8;
4. Malic acid (stereochemistry not specified), 617-48-1;
5. Microcrystalline cellulose, 9004-34-6;
6. Poloxamer (grade not specified), 9003-11-6;
7. Isotridecyl alcohol (branched C13 isomer mixture), 27458‑92‑0.

Контрольный прогон сделан на Carbomer, colloidal silicon dioxide и Poloxamer.
Исправленный план сохраняет товарное имя закупщика, выносит полезное уточнение
в отдельный запрос и не отправляет поисковику фразу `grade not specified`.
Это важно, потому что справочное IUPAC-имя `dioxosilane` хуже описывает искомый
грейд fumed/colloidal silica, а общий CAS Poloxamer 9003-11-6 используется как
минимум для грейдов 188 и 407. Поэтому Poloxamer без номера грейда должен
оставаться неоднозначным, а не автоматически считаться точным совпадением.

Первичные ориентиры:

- Evonik AEROSIL 200: https://www.evonik.com/en/products/se/pr_52000005.html
- BASF Kolliphor Poloxamers: https://download.basf.com/p1/EN_StaticDocuments_42615/en/Kolliphor_poloxamers_Multi-functional_ingredients_for_topical_formulation_design
- Newman Carbomer: https://www.nmcarbomer.com/uploadfile/attachment/0085bde4a651e726ddd06e8920e2e62a.pdf

### Сложные продукты без CAS

`C12-C15 fatty alcohol blend` после исправления запускается без PubChem и дал
32 результата без ошибок. В верхней части есть профильные страницы Sunwise,
Rickman и Musim Mas. Однако само обозначение C12-C15 не задаёт распределение
углеродных цепей, линейность и происхождение сырья, поэтому эти параметры должны
быть частью спецификации и проверки.

`Cellactose 80` дал 21 результат. Официальная страница MEGGLE содержит точный
состав: 75% alpha-lactose monohydrate и 25% powdered cellulose. После добавления
CPHI, Pharma Excipients, Tracxn и Barentz в реестр посредников официальный
производитель остаётся в верхней проверяемой группе, а каталоги не расходуют
лимит загрузки страниц.

Первичные ориентиры:

- MEGGLE Cellactose 80: https://www.meggle-excipients.com/products/cellactose-80
- Sasol fatty alcohols: https://chemicals.sasol.com/products/fatty-alcohols-as-building-blocks-for-paints
- Musim Mas fatty alcohols: https://www.musimmas.com/products-applications/products/fatty-alcohols/

### Аналог DOWSIL 9045

Официальный эталон Dow задаёт не только INCI, но и измеримые границы:

- Cyclopentasiloxane (and) Dimethicone Crosspolymer;
- вязкость 350 000–550 000 cP;
- нелетучий остаток 12–12,75%;
- безводный гель и заявленные области применения.

Запросы, в которых в каждой строке присутствовал `DOWSIL 9045`, вернули
12 результатов, почти полностью состоящих из Dow, дистрибьюторов и справочников.
После добавления отдельного запроса по функциональному имени и INCI верх выдачи
заняли китайские кандидаты Wednsday Chem, Hony и Silibase.

Это кандидаты, а не подтверждённые эквиваленты. У Wednsday указан
Dimethicone/Vinyl Dimethicone Crosspolymer, то есть состав уже отличается от
эталона. У Hony 9040 совпадает INCI, но открытая карточка не подтверждает весь
диапазон вязкости и нелетучего остатка DOWSIL 9045. Оба случая требуют TDS/CoA,
сопоставления свойств и решения специалиста.

Первичные страницы:

- Dow DOWSIL 9045: https://www.dow.com/en-us/pdp.dowsil-9045-silicone-elastomer-blend.04017056z.html
- Hony silicone elastomer blend: https://www.hony-chem.com/Silicone-Elastomer-Blends-Cyclopentasiloxane-Dimethicone-Crosspolymer-pd596717278.html
- Wednsday Chem candidate: https://www.wednsday-chem.com/products/sensory-modifier/silicone-elastomer-blend/cyclopentasiloxane-and-dimethicone-vinyl-dimethicone-crosspolymer-cas-541-02-6.html

### Второй цикл сложных позиций

На локальном сайте создана реальная тестовая заявка на функциональный аналог
`DOWSIL 5-7113 Silicone Quat Microemulsion` для Китая и Индии. В неё вошли INCI,
22% активного содержания, pH 6–8 и запрет считать дистрибьютора изготовителем.
Это выявило и устранило два UI-дефекта: `/requests/new` открывал таблицу вместо
формы, а критерии эквивалентности аналога не сохранялись в `specification`.

После этого проверены двенадцать более сложных позиций:

| Позиция | Результат независимой проверки |
|---|---|
| Ademetionine | Свободное имя недостаточно: в продаже встречается, например, disulfate tosylate; соль и форма должны быть заданы до квалификации. |
| Amikacin | Свободное основание и sulfate теперь ищутся отдельными ветками; sulfate имеет отдельный CAS 39831-55-5. |
| Colistin | Свободная форма и sulfate также разделены; для sulfate используется CAS 1264-72-8, а не общий текст из скобок. |
| Cetomacrogol | `Cetomacrogol 1000` является определённым грейдом; запрос без номера грейда остаётся неоднозначным. |
| Citrate / citric acid salt | Без катиона нельзя выбрать sodium/potassium/calcium citrate. Проект сохраняет широкий поиск и не придумывает соль. |
| Augeo commercial solvent | Функциональная ветка по `isopropylidene glycerol / solketal` находит китайские продуктовые страницы, но соответствие фирменному грейду требует TDS. |
| Frescolat | Это линейка охлаждающих агентов, а не одно вещество. Подмена общего названия на menthyl lactate допустима только после указания грейда Frescolat ML. |
| DOWSIL 5-7113 | По полному INCI находится DOWSIL CE-7114 с тем же составом, но это тот же производитель; независимый полностью подтверждённый эквивалент в первом проходе не найден. |
| XIAMETER AFE-0100 | Китайские 30%-ные food-grade silicone antifoam существуют, но найденный кандидат с pH 6–8 не повторяет pH 3,5 и вязкость 50 000 cP эталона. |
| XIAMETER PMX-200 100 cSt | Безбрендовый запрос `100% PDMS + 100 cSt` находит несколько китайских продуктовых страниц; это наиболее перспективный аналоговый кейс выборки. |
| ABIL 45 ME | Официальный INCI даёт хороший композиционный якорь, но независимый продукт с полностью подтверждённым составом пока не найден. |
| ABIL T Quat 60 | Официально подтверждена функция silicone conditioning agent, но открытого состава недостаточно для безопасного поиска эквивалента; нужен TDS/INCI. |

Первичные ориентиры второго цикла:

- DOWSIL 5-7113: https://www.dow.com/en-us/pdp.dowsil-5-7113-silicone-quat-microemulsion.04017042z.html
- DOWSIL CE-7114: https://www.dow.com/ja-jp/pdp.dowsil-ce-7114-silicone-quat-microemulsion.04096869z.html
- XIAMETER AFE-0100: https://www.dow.com/en-us/pdp.xiameter-afe-0100-antifoam-emulsion-food-grade.01014137z.html
- XIAMETER PMX-200 100 cSt: https://www.dow.com/en-us/pdp.xiameter-pmx-200-silicone-fluid-100-cst.01013190z.html
- Evonik ABIL ME 45 MB: https://www.evonik.com/en/products/cs/pr_52023217.html
- Evonik ABIL T Quat 60: https://personal-care.evonik.com/en/attachment/2832?rev=1
- Solvay Augeo Clean Multi: https://www.solvay.com/en/product/augeo-clean-multi
- Symrise Frescolat range: https://www.symrise.com/scent-and-care/cosmetic-ingredients/actives/
- Capot Solketal 100-79-8: https://www.capotchem.cn/doc/spec_100-79-8.do
- SiSiB 100 cSt dimethicone TDS: https://www.sinosil.com/uploads/file/20250314/sisib-pc12010-100-tds.pdf
- WHO, Cetomacrogol 1000: https://iris.who.int/bitstream/handle/10665/38009/9241544627_eng.pdf?sequence=4

Платный поисковый API на локальном стенде не настроен. Публичный DuckDuckGo
вернул антибот-страницу, поэтому приложение корректно завершило синхронный
прогон ошибкой источника и не выдало пустую подборку за успешный результат.
Фактическая выдача для сравнения получена через веб-поиск Codex; изменение
fallback-плана проверено детерминированными тестами без обхода защиты сайта.

## Исправления продукта

- поиск и очередь теперь проходят без CAS; PubChem в этом режиме не вызывается;
- в worker передаются способ идентификации RFQ, эталон аналога, допустимые
  вариации, спецификация и применение;
- для смеси поиск использует название и спецификацию, а интерфейс не показывает
  `CAS: null` и не предлагает сохранить CAS-карточку вещества;
- для аналога строятся две ветви: бренд + `equivalent/alternative/substitute` и
  функциональное описание + композиционный якорь без бренда;
- режим спецификации использует саму спецификацию в fallback-поиске, а не только
  товарное имя;
- явно перечисленные `free base or sulfate` формы ищутся раздельно; служебная
  фраза `not separated` не отправляется поисковику;
- полный торговый идентификатор вида `DOWSIL 5-7113 ...` разбирается на брендовый
  и функциональный якорь даже когда имя и эталон совпадают;
- альтернативный продукт никогда не получает автоматический exact/shortlist:
  требуется ручная проверка состава, свойств и грейда;
- описательные комментарии закупщика не подменяют основное товарное имя;
- результаты с другим явно указанным валидным CAS отбрасываются до загрузки;
- нормативные PDF, декларации состава, каталоги, лабораторные продавцы и
  дистрибьюторы понижены или вынесены из бюджета поиска изготовителей;
- 403/429/503 от DuckDuckGo останавливает повтор одинаковых запросов вместо
  нескольких длительных пауз;
- утверждение о партнёрской или контрактной производственной базе больше не
  подтверждает собственную роль производителя.

## Ограничения и следующий шаг

Проверенный набор пока является стратифицированной выборкой, а не прогоном всех
323 строк через платный поисковый API. Следующий измеримый этап — расширить
регрессионный набор на все семь средних позиций, 10–15 разных смесей без CAS и
по одному представителю каждой торговой линейки DOWSIL/XIAMETER/SYL-OFF/ABIL.
Для аналогов нужно хранить отдельную таблицу требований эталона (состав, функция,
вязкость, активный остаток, носитель, форма, применение) и сравнивать каждый
кандидат по полям, не сворачивая результат в один «процент похожести».
