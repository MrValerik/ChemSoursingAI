ALTER TABLE communication_test_messages
    DROP COLUMN translation_ru;

ALTER TABLE communication_test_runs
    ALTER COLUMN subject SET DEFAULT 'Тест ChemSource AI';
