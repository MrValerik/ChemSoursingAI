-- Места у локальной модели, общие для всех worker-процессов.
--
-- Воркеров намеренно больше, чем слотов у llama-server: половину времени
-- поиск занимает загрузка страниц, и на это время место у модели держать
-- незачем. Но сами обращения к модели этой арифметике не подчиняются.
--
-- Замер на стенде: три параллельных поиска на двух слотах. Ожидание в
-- очереди сервера превысило таймаут запроса, тайм-аут поднялся как
-- «модель недоступна», этап перезапустился и снова стал третьим. Семь
-- отказов подряд, сорок четыре минуты без результата — при живой модели.

CREATE TABLE IF NOT EXISTS llm_slots (
    id SERIAL PRIMARY KEY,
    owner VARCHAR(120),
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_llm_slots_expires_at ON llm_slots (expires_at);

-- Две строки под конфигурацию llama-server с --parallel 2. Число строк
-- приводится к настройке при первом обращении, поэтому здесь достаточно
-- стартового значения.
INSERT INTO llm_slots (owner, expires_at)
SELECT NULL, NULL
FROM generate_series(1, 2)
WHERE NOT EXISTS (SELECT 1 FROM llm_slots);
