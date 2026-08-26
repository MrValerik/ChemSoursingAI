ALTER TABLE quotations
    ADD COLUMN manufacturer VARCHAR(255),
    ADD COLUMN origin_country VARCHAR(120),
    ADD COLUMN packaging VARCHAR(255),
    ADD COLUMN price_unit VARCHAR(32),
    ADD COLUMN quoted_quantity VARCHAR(64),
    ADD COLUMN total_price NUMERIC(14, 4),
    ADD COLUMN delivery_cost NUMERIC(14, 4),
    ADD COLUMN duty_cost NUMERIC(14, 4),
    ADD COLUMN vat_cost NUMERIC(14, 4),
    ADD COLUMN landed_cost NUMERIC(14, 4),
    ADD COLUMN cost_currency VARCHAR(3),
    ADD COLUMN is_hazmat BOOLEAN;
