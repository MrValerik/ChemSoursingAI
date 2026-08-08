"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { phoneNumberFromWid, resolveSenderNumber } = require("./sender-number");

test("classic WhatsApp IDs are converted directly to phone numbers", async () => {
  assert.equal(phoneNumberFromWid("79000000000@c.us"), "79000000000");
  assert.equal(phoneNumberFromWid("123456789012345@lid"), null);
  assert.equal(
    await resolveSenderNumber({ from: "79000000000@c.us" }, null),
    "79000000000",
  );
});

test("modern LID senders are resolved through the library phone mapping", async () => {
  const requested = [];
  const client = {
    async getContactLidAndPhone(ids) {
      requested.push(ids);
      return [{ lid: ids[0], pn: "79000000000@c.us" }];
    },
  };
  const message = { from: "123456789012345@lid" };

  assert.equal(await resolveSenderNumber(message, client), "79000000000");
  assert.deepEqual(requested, [["123456789012345@lid"]]);
});

test("an unresolved LID is never mistaken for a supplier phone number", async () => {
  const client = { async getContactLidAndPhone() { return [{}]; } };
  const message = {
    from: "123456789012345@lid",
    async getContact() {
      return { number: "123456789012345", id: { user: "123456789012345", server: "lid" } };
    },
  };

  assert.equal(await resolveSenderNumber(message, client), null);
});
