"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const express = require("express");
const QRCode = require("qrcode");
const { Client, LocalAuth } = require("whatsapp-web.js");

const port = Number(process.env.PORT || 3000);
const serviceToken = process.env.WHATSAPP_WEB_SERVICE_TOKEN || "";
const callbackUrl = process.env.WHATSAPP_WEB_CALLBACK_URL || "";
const dataDir = process.env.WHATSAPP_WEB_DATA_DIR || "/data";
const eventFile = path.join(dataDir, "events", "pending.json");
const callbackTimeoutMs = Number(process.env.WHATSAPP_WEB_CALLBACK_TIMEOUT_MS || 15000);

let client = null;
let state = "stopped";
let qrDataUrl = null;
let account = null;
let lastError = null;
let initializing = null;
let pendingEvents = [];
let flushing = false;

fs.mkdirSync(path.dirname(eventFile), { recursive: true });
try {
  pendingEvents = JSON.parse(fs.readFileSync(eventFile, "utf8"));
  if (!Array.isArray(pendingEvents)) pendingEvents = [];
} catch (_error) {
  pendingEvents = [];
}

function persistEvents() {
  const temporary = `${eventFile}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(pendingEvents));
  fs.renameSync(temporary, eventFile);
}

function safeEqual(left, right) {
  const a = Buffer.from(left || "");
  const b = Buffer.from(right || "");
  return a.length === b.length && a.length > 0 && crypto.timingSafeEqual(a, b);
}

function authorize(req, res, next) {
  const supplied = (req.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!serviceToken || !safeEqual(supplied, serviceToken)) {
    return res.status(401).json({ detail: "unauthorized" });
  }
  return next();
}

function statusPayload() {
  return {
    state,
    ready: state === "ready",
    qr_available: Boolean(qrDataUrl),
    account,
    pending_events: pendingEvents.length,
    error: lastError,
  };
}

async function flushEvents() {
  if (flushing || !callbackUrl || !serviceToken || pendingEvents.length === 0) return;
  flushing = true;
  try {
    while (pendingEvents.length > 0) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), callbackTimeoutMs);
      try {
        const response = await fetch(callbackUrl, {
          method: "POST",
          headers: {
            authorization: `Bearer ${serviceToken}`,
            "content-type": "application/json",
          },
          body: JSON.stringify(pendingEvents[0]),
          signal: controller.signal,
        });
        if (!response.ok) break;
        pendingEvents.shift();
        persistEvents();
      } catch (_error) {
        break;
      } finally {
        clearTimeout(timer);
      }
    }
  } finally {
    flushing = false;
  }
}

async function queueIncoming(message) {
  const from = String(message.from || "");
  if (
    message.fromMe ||
    !message.body ||
    from.endsWith("@g.us") ||
    from === "status@broadcast"
  ) return;
  let sender = from.replace(/@(c|s)\.us$/, "");
  try {
    const contact = await message.getContact();
    sender = String(contact.number || contact.id && contact.id.user || sender);
  } catch (_error) {
    // The raw sender is still usable for classic phone-number chat IDs.
  }
  pendingEvents.push({
    event: "message",
    message_id: String(message.id && message.id._serialized || ""),
    from: sender,
    body: String(message.body).slice(0, 8000),
    timestamp: Number(message.timestamp || Math.floor(Date.now() / 1000)),
  });
  persistEvents();
  void flushEvents();
}

async function createClient() {
  if (client) return client;
  state = "initializing";
  lastError = null;
  const instance = new Client({
    authStrategy: new LocalAuth({ clientId: "chemsource", dataPath: path.join(dataDir, "auth") }),
    puppeteer: {
      executablePath: process.env.CHROME_BIN || "/usr/bin/chromium",
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
    },
  });
  instance.on("qr", async (qr) => {
    state = "qr";
    account = null;
    qrDataUrl = await QRCode.toDataURL(qr, { errorCorrectionLevel: "M", margin: 2, width: 360 });
  });
  instance.on("authenticated", () => { state = "authenticated"; qrDataUrl = null; });
  instance.on("ready", () => {
    state = "ready";
    qrDataUrl = null;
    account = instance.info && instance.info.wid ? instance.info.wid.user : null;
    void flushEvents();
  });
  instance.on("auth_failure", () => { state = "auth_failure"; lastError = "authentication_failed"; });
  instance.on("disconnected", () => { state = "disconnected"; account = null; });
  instance.on("message", (message) => void queueIncoming(message));
  client = instance;
  initializing = instance.initialize().catch((error) => {
    state = "error";
    lastError = error && error.name ? error.name : "initialization_failed";
    client = null;
    return null;
  }).finally(() => { initializing = null; });
  return instance;
}

async function startClient() {
  const instance = await createClient();
  if (initializing) await initializing;
  return instance;
}

const app = express();
app.use(express.json({ limit: "32kb" }));

app.get("/health", (_req, res) => res.json({ ok: true, state }));
app.use(authorize);
app.get("/status", (_req, res) => res.json(statusPayload()));
app.get("/qr", (_req, res) => {
  if (!qrDataUrl) return res.status(404).json({ detail: "qr_not_available" });
  return res.json({ qr_data_url: qrDataUrl });
});
app.post("/connect", async (_req, res) => {
  try {
    void startClient().catch(() => {});
    return res.status(202).json(statusPayload());
  } catch (_error) {
    return res.status(503).json(statusPayload());
  }
});
app.post("/disconnect", async (_req, res) => {
  const instance = client;
  client = null;
  qrDataUrl = null;
  account = null;
  state = "stopped";
  if (instance) {
    try { await instance.logout(); } catch (_error) { await instance.destroy().catch(() => {}); }
  }
  return res.json(statusPayload());
});
app.post("/messages", async (req, res) => {
  if (state !== "ready" || !client) return res.status(409).json({ detail: "whatsapp_not_ready" });
  const recipient = String(req.body.to || "").replace(/\D/g, "");
  const body = String(req.body.body || "").trim();
  if (recipient.length < 8 || recipient.length > 15 || !body) {
    return res.status(422).json({ detail: "invalid_message" });
  }
  try {
    const registered = await client.isRegisteredUser(`${recipient}@c.us`);
    if (!registered) return res.status(422).json({ detail: "recipient_not_registered" });
    const message = await client.sendMessage(`${recipient}@c.us`, body.slice(0, 4096));
    return res.status(201).json({ message_id: message.id._serialized });
  } catch (_error) {
    return res.status(502).json({ detail: "delivery_failed" });
  }
});

app.listen(port, "0.0.0.0", () => {
  if (serviceToken) void createClient().catch(() => {});
});
setInterval(() => void flushEvents(), 15000).unref();
