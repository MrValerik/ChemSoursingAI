"use strict";

const crypto = require("crypto");

function providerMessageId(message) {
  const candidates = [
    message && message.id && message.id._serialized,
    message && typeof message.id === "string" ? message.id : null,
    message && message.id && message.id.id,
    message && message._data && message._data.id && message._data.id._serialized,
    message && message._data && message._data.id && message._data.id.id,
  ];
  const value = candidates.find(
    (candidate) => typeof candidate === "string" && candidate.trim(),
  );
  return value ? value.trim() : null;
}

function fallbackInboundMessageId(event) {
  const fingerprint = JSON.stringify({
    from: String(event.from || ""),
    body: String(event.body || ""),
    timestamp: Number(event.timestamp || 0),
  });
  return `web-in-${crypto.createHash("sha256").update(fingerprint).digest("hex")}`;
}

function normalizedPendingEvent(event) {
  if (typeof event.message_id === "string" && event.message_id.trim()) return event;
  return { ...event, message_id: fallbackInboundMessageId(event) };
}

module.exports = {
  fallbackInboundMessageId,
  normalizedPendingEvent,
  providerMessageId,
};
