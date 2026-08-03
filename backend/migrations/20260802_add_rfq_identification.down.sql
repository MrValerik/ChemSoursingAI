-- Откат снимает добавленные колонки. Обязательность CAS восстанавливается
-- только если запросов без номера не появилось: откат деплоя не повод
-- удалять заведённые закупщиком запросы, а SET NOT NULL на NULL-значениях
-- упадёт. Асимметрия здесь намеренная — данные важнее симметрии.

DROP INDEX IF EXISTS ix_rfqs_identification_method;

ALTER TABLE rfqs DROP COLUMN IF EXISTS field_sources;
ALTER TABLE rfqs DROP COLUMN IF EXISTS excluded_names;
ALTER TABLE rfqs DROP COLUMN IF EXISTS confirmed_synonyms;
ALTER TABLE rfqs DROP COLUMN IF EXISTS specification;
ALTER TABLE rfqs DROP COLUMN IF EXISTS analog_variations;
ALTER TABLE rfqs DROP COLUMN IF EXISTS analog_reference;
ALTER TABLE rfqs DROP COLUMN IF EXISTS identification_method;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM rfqs WHERE cas IS NULL) THEN
        ALTER TABLE rfqs ALTER COLUMN cas SET NOT NULL;
    END IF;
END $$;
