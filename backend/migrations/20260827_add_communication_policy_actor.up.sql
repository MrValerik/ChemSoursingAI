ALTER TABLE communication_policy_audits
    ADD COLUMN actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

UPDATE communication_policy_audits AS audit
SET actor_id = COALESCE(
    (
        SELECT test_run.actor_id
        FROM communication_test_runs AS test_run
        WHERE test_run.id = audit.test_run_id
    ),
    (
        SELECT rfq.owner_id
        FROM rfqs AS rfq
        WHERE rfq.id = audit.rfq_id
    )
)
WHERE audit.actor_id IS NULL;

CREATE INDEX ix_communication_policy_audits_actor_id
    ON communication_policy_audits (actor_id);
