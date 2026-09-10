ALTER TABLE echemi_searches ADD COLUMN rfq_id INTEGER REFERENCES rfqs(id) ON DELETE CASCADE;
CREATE INDEX ix_echemi_searches_rfq_id ON echemi_searches(rfq_id);
