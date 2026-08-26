ALTER TABLE quotations
    DROP COLUMN IF EXISTS is_hazmat,
    DROP COLUMN IF EXISTS cost_currency,
    DROP COLUMN IF EXISTS landed_cost,
    DROP COLUMN IF EXISTS vat_cost,
    DROP COLUMN IF EXISTS duty_cost,
    DROP COLUMN IF EXISTS delivery_cost,
    DROP COLUMN IF EXISTS total_price,
    DROP COLUMN IF EXISTS quoted_quantity,
    DROP COLUMN IF EXISTS price_unit,
    DROP COLUMN IF EXISTS packaging,
    DROP COLUMN IF EXISTS origin_country,
    DROP COLUMN IF EXISTS manufacturer;
