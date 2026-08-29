CREATE TABLE IF NOT EXISTS purchase_history_entries (
    id SERIAL PRIMARY KEY,
    rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
    quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE RESTRICT,
    substance_id INTEGER REFERENCES substances(id) ON DELETE SET NULL,
    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    intermediary_id INTEGER REFERENCES intermediaries(id) ON DELETE SET NULL,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note TEXT,
    snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_purchase_history_entries_rfq_id
    ON purchase_history_entries (rfq_id);
CREATE INDEX IF NOT EXISTS ix_purchase_history_entries_quotation_id
    ON purchase_history_entries (quotation_id);
CREATE INDEX IF NOT EXISTS ix_purchase_history_entries_substance_id
    ON purchase_history_entries (substance_id);
CREATE INDEX IF NOT EXISTS ix_purchase_history_entries_supplier_id
    ON purchase_history_entries (supplier_id);
CREATE INDEX IF NOT EXISTS ix_purchase_history_entries_intermediary_id
    ON purchase_history_entries (intermediary_id);
CREATE INDEX IF NOT EXISTS ix_purchase_history_entries_actor_id
    ON purchase_history_entries (actor_id);

-- Существующий последний выбор становится первой записью истории. Старые
-- решения не должны исчезнуть только потому, что аудит появился позднее.
INSERT INTO purchase_history_entries (
    rfq_id,
    quotation_id,
    substance_id,
    supplier_id,
    actor_id,
    note,
    snapshot,
    created_at
)
SELECT
    decision.rfq_id,
    decision.quotation_id,
    rfq.substance_id,
    supplier.id,
    decision.selected_by_id,
    decision.note,
    jsonb_build_object(
        'rfq_name', rfq.name,
        'rfq_cas', rfq.cas,
        'rfq_volume', rfq.volume,
        'supplier_name', supplier.company,
        'supplier_type', supplier.type,
        'manufacturer', quotation.manufacturer,
        'origin_country', quotation.origin_country,
        'price', quotation.price,
        'currency', quotation.currency,
        'cost_currency', quotation.cost_currency,
        'price_unit', quotation.price_unit,
        'quoted_quantity', quotation.quoted_quantity,
        'total_price', quotation.total_price,
        'delivery_cost', quotation.delivery_cost,
        'duty_cost', quotation.duty_cost,
        'vat_cost', quotation.vat_cost,
        'landed_cost', quotation.landed_cost,
        'moq', quotation.moq,
        'incoterm', quotation.incoterm,
        'payment_terms', quotation.payment_terms,
        'lead_time', quotation.lead_time,
        'has_coa', quotation.has_coa,
        'has_tds', quotation.has_tds,
        'is_complete', quotation.is_complete
    ),
    decision.updated_at
FROM purchase_decisions AS decision
JOIN rfqs AS rfq ON rfq.id = decision.rfq_id
JOIN quotations AS quotation ON quotation.id = decision.quotation_id
LEFT JOIN managers AS manager ON manager.id = quotation.manager_id
LEFT JOIN suppliers AS supplier ON supplier.id = manager.supplier_id
WHERE NOT EXISTS (
    SELECT 1
    FROM purchase_history_entries AS existing
    WHERE existing.rfq_id = decision.rfq_id
      AND existing.quotation_id = decision.quotation_id
      AND existing.created_at = decision.updated_at
);
