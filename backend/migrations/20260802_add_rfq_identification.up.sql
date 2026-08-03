-- Способ идентификации предмета закупки.
--
-- Раньше запрос нельзя было сохранить без CAS-номера. Но номер есть не у
-- всего, что закупают: у смесей, рецептур, полимеров и промышленных
-- продуктов его нет и не будет. Такой запрос — не «неизвестная молекула»,
-- а спецификация, и отправить по нему RFQ вполне можно.
--
-- Три способа задать предмет закупки:
--   cas     — точная молекула по номеру;
--   analog  — «как вот это вещество, но с оговорками»;
--   spec    — назначение и требования, молекула не важна.

ALTER TABLE rfqs
    ALTER COLUMN cas DROP NOT NULL;

ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS identification_method VARCHAR(16)
        NOT NULL DEFAULT 'cas';

-- Эталонное вещество для режима analog и то, чем от него можно отступить
-- (соль, чистота, форма, производитель). Без второго поля «аналог»
-- означает сразу всё перечисленное, и текст письма поставщику собрать
-- нельзя: неизвестно, что именно допустимо заменить.
ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS analog_reference VARCHAR(255);
ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS analog_variations JSONB;

-- Требования для режима spec: чистота и применение уже есть отдельными
-- полями, здесь остальное свободным текстом.
ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS specification TEXT;

-- Названия, которые закупщик отметил как подходящие, и те, что снял.
-- Без CAS-номера якорем поиска служит название, а оно неуникально: у
-- бетаина и его гидрохлорида названия соседние, а вещества разные.
-- Поэтому отметки человека — не косметика, а то, чем в этой ветке
-- держится точность. Снятые названия работают отрицательным фильтром.
ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS confirmed_synonyms JSONB;
ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS excluded_names JSONB;

-- Источник каждого поля: pubchem / ai_agent / human / catalog. Хранится
-- рядом со значением, чтобы находка ИИ-агента не выглядела справочными
-- данными через месяц после ввода.
ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS field_sources JSONB;

CREATE INDEX IF NOT EXISTS ix_rfqs_identification_method
    ON rfqs (identification_method);

-- Существующие запросы созданы по номеру — значение по умолчанию для них
-- верно, отдельный backfill не нужен.
