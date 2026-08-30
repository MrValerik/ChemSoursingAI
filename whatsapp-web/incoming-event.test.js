"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { buildIncomingEvent } = require("./incoming-event");

test("builds a two-way event with quoted id and document", async () => {
  const event = await buildIncomingEvent(
    {
      from: "79005550102@c.us",
      fromMe: false,
      body: "Please see the CoA",
      timestamp: 123,
      id: { _serialized: "incoming-1" },
      hasMedia: true,
      hasQuotedMsg: true,
      downloadMedia: async () => ({
        data: Buffer.from("coa").toString("base64"),
        mimetype: "application/pdf",
        filename: "coa.pdf",
      }),
      getQuotedMessage: async () => ({ id: { _serialized: "outbound-1" } }),
    },
    null,
    1024,
  );

  assert.equal(event.message_id, "incoming-1");
  assert.equal(event.from, "79005550102");
  assert.equal(event.quoted_message_id, "outbound-1");
  assert.equal(event.attachments[0].filename, "coa.pdf");
  assert.equal(event.attachments[0].content_base64, "Y29h");
});

test("keeps oversized media metadata without placing the file in the queue", async () => {
  const event = await buildIncomingEvent(
    {
      from: "79005550102@c.us",
      fromMe: false,
      body: "",
      timestamp: 124,
      id: { _serialized: "incoming-2" },
      hasMedia: true,
      hasQuotedMsg: false,
      downloadMedia: async () => ({
        data: Buffer.from("too large").toString("base64"),
        mimetype: "text/plain",
        filename: "large.txt",
      }),
    },
    null,
    2,
  );

  assert.equal(event.body, "");
  assert.equal(event.attachments[0].error, "media_too_large");
  assert.equal(event.attachments[0].content_base64, undefined);
});
