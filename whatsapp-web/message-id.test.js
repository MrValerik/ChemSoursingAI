"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  normalizedPendingEvent,
  providerMessageId,
} = require("./message-id");

test("providerMessageId supports current and legacy WhatsApp message shapes", () => {
  assert.equal(providerMessageId({ id: { _serialized: " current-id " } }), "current-id");
  assert.equal(providerMessageId({ id: "string-id" }), "string-id");
  assert.equal(providerMessageId({ id: { id: "nested-id" } }), "nested-id");
  assert.equal(
    providerMessageId({ _data: { id: { _serialized: "legacy-id" } } }),
    "legacy-id",
  );
  assert.equal(providerMessageId({}), null);
});

test("missing inbound IDs receive a stable non-empty fallback", () => {
  const event = {
    event: "message",
    message_id: "",
    from: "79000000000",
    body: "Price is USD 2/kg",
    timestamp: 1786200000,
  };
  const first = normalizedPendingEvent(event);
  const second = normalizedPendingEvent({ ...event });

  assert.match(first.message_id, /^web-in-[a-f0-9]{64}$/);
  assert.equal(first.message_id, second.message_id);
  assert.strictEqual(normalizedPendingEvent(first), first);
});
