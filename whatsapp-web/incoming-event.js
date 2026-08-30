"use strict";

const path = require("path");
const { normalizedPendingEvent, providerMessageId } = require("./message-id");
const { resolveSenderNumber } = require("./sender-number");

function mediaFilename(message, media, timestamp) {
  const original =
    (media && media.filename) ||
    (message && message._data && message._data.filename) ||
    `whatsapp-${timestamp}`;
  return path.basename(String(original)).slice(0, 255) || `whatsapp-${timestamp}`;
}

async function quotedMessageId(message) {
  if (!message || !message.hasQuotedMsg || typeof message.getQuotedMessage !== "function") {
    return null;
  }
  try {
    const quoted = await message.getQuotedMessage();
    return providerMessageId(quoted);
  } catch (_error) {
    return null;
  }
}

async function incomingAttachment(message, timestamp, maxMediaBytes) {
  if (!message || !message.hasMedia || typeof message.downloadMedia !== "function") {
    return null;
  }
  try {
    const media = await message.downloadMedia();
    if (!media || !media.data) {
      return {
        filename: mediaFilename(message, media, timestamp),
        content_type: String((media && media.mimetype) || "application/octet-stream").slice(0, 255),
        size: 0,
        error: "media_download_failed",
      };
    }
    const size = Buffer.byteLength(media.data, "base64");
    const attachment = {
      filename: mediaFilename(message, media, timestamp),
      content_type: String(media.mimetype || "application/octet-stream").slice(0, 255),
      size,
    };
    if (size > maxMediaBytes) {
      return { ...attachment, error: "media_too_large" };
    }
    return { ...attachment, content_base64: String(media.data) };
  } catch (_error) {
    return {
      filename: mediaFilename(message, null, timestamp),
      content_type: String(
        (message && message._data && message._data.mimetype) ||
          "application/octet-stream",
      ).slice(0, 255),
      size: 0,
      error: "media_download_failed",
    };
  }
}

async function buildIncomingEvent(message, client, maxMediaBytes) {
  const from = String((message && message.from) || "");
  if (
    !message ||
    message.fromMe ||
    from.endsWith("@g.us") ||
    from === "status@broadcast"
  ) return null;
  const sender = await resolveSenderNumber(message, client);
  if (!sender) return null;
  const body = String(message.body || "").slice(0, 8000);
  if (!body && !message.hasMedia) return null;
  const timestamp = Number(message.timestamp || Math.floor(Date.now() / 1000));
  const attachment = await incomingAttachment(message, timestamp, maxMediaBytes);
  const event = {
    event: "message",
    message_id: providerMessageId(message) || "",
    from: sender,
    body,
    timestamp,
    quoted_message_id: await quotedMessageId(message),
    attachments: attachment ? [attachment] : [],
  };
  return normalizedPendingEvent(event);
}

module.exports = {
  buildIncomingEvent,
  incomingAttachment,
  mediaFilename,
  quotedMessageId,
};
