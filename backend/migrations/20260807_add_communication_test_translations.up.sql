ALTER TABLE communication_test_messages
    ADD COLUMN translation_ru TEXT;

ALTER TABLE communication_test_runs
    ALTER COLUMN subject SET DEFAULT 'Request for quotation';
