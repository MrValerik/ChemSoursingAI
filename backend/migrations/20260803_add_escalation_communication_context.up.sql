ALTER TABLE escalations
    ADD COLUMN communication_id INTEGER REFERENCES communications(id) ON DELETE SET NULL;

ALTER TABLE escalations
    ADD COLUMN manager_id INTEGER REFERENCES managers(id) ON DELETE SET NULL;

CREATE INDEX ix_escalations_communication_id
    ON escalations (communication_id);

CREATE INDEX ix_escalations_manager_id
    ON escalations (manager_id);
