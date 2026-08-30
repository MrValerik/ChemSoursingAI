"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const express = require("express");
const QRCode = require("qrcode");
const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");
const {
  normalizedPendingEvent,
  providerMessageId,
} = require("./message-id");
const { buildIncomingEvent } = require("./incoming-event");
const { removeStaleChromiumLocks } = require("./session-files");

const port = Number(process.env.PORT || 3000);
const serviceToken = process.env.WHATSAPP_WEB_SERVICE_TOKEN || "";
const callbackUrl = process.env.WHATSAPP_WEB_CALLBACK_URL || "";
const dataDir = process.env.WHATSAPP_WEB_DATA_DIR || "/data";
const eventFile = path.join(dataDir, "events", "pending.json");
const callbackTimeoutMs = Number(process.env.WHATSAPP_WEB_CALLBACK_TIMEOUT_MS || 15000);
const proxyServer = String(process.env.WHATSAPP_WEB_PROXY_SERVER || "").trim();
const initRetryMs = Math.max(
  5000,
  Number(process.env.WHATSAPP_WEB_INIT_RETRY_MS || 15000),
);
const configuredMaxMediaMb = Number(process.env.WHATSAPP_WEB_MAX_MEDIA_MB || 25);
const maxMediaMb = Number.isFinite(configuredMaxMediaMb) && configuredMaxMediaMb > 0
  ? Math.min(configuredMaxMediaMb, 25)
  : 25;
const maxMediaBytes = maxMediaMb * 1024 * 1024;

let client = null;
let state = "stopped";
let qrDataUrl = null;
let pairingCode = null;
let pairingCodeExpiresAt = 0;
let account = null;
let lastError = null;
let clientState = null;
let loadingPercent = null;
let initializing = null;
let retryTimer = null;
let shouldRun = Boolean(serviceToken);
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
  if (pairingCodeExpiresAt && pairingCodeExpiresAt <= Date.now()) {
    pairingCode = null;
    pairingCodeExpiresAt = 0;
  }
  return {
    state,
    ready: state === "ready",
    qr_available: Boolean(qrDataUrl),
    pairing_code_available: Boolean(pairingCode),
    pairing_code_expires_in_seconds: pairingCodeExpiresAt
      ? Math.max(0, Math.ceil((pairingCodeExpiresAt - Date.now()) / 1000))
      : 0,
    client_state: clientState,
    loading_percent: loadingPercent,
    proxy_configured: Boolean(proxyServer),
    account,
    pending_events: pendingEvents.length,
    error: lastError,
  };
}

function safeInitializationError(error) {
  const detail = String(error && error.message ? error.message : error || "");
  if (/ERR_PROXY_CONNECTION_FAILED|ERR_TUNNEL_CONNECTION_FAILED/i.test(detail)) {
    return "proxy_connection_failed";
  }
  if (/ERR_NAME_NOT_RESOLVED|ENOTFOUND|EAI_AGAIN/i.test(detail)) {
    return "whatsapp_web_dns_failed";
  }
  if (/timeout|ERR_TIMED_OUT|UND_ERR_CONNECT_TIMEOUT/i.test(detail)) {
    return proxyServer
      ? "whatsapp_web_proxy_timeout"
      : "whatsapp_web_connection_timeout";
  }
  return "whatsapp_web_initialization_failed";
}

function scheduleInitializationRetry() {
  if (!shouldRun || retryTimer) return;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    if (shouldRun && !client) void startClient().catch(() => {});
  }, initRetryMs);
  retryTimer.unref();
}

async function flushEvents() {
  if (flushing || !callbackUrl || !serviceToken || pendingEvents.length === 0) return;
  flushing = true;
  try {
    while (pendingEvents.length > 0) {
      const normalized = normalizedPendingEvent(pendingEvents[0]);
      if (normalized !== pendingEvents[0]) {
        pendingEvents[0] = normalized;
        persistEvents();
      }
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
  const event = await buildIncomingEvent(message, client, maxMediaBytes);
  if (!event) return;
  pendingEvents.push(event);
  persistEvents();
  void flushEvents();
}

async function createClient() {
  if (client) return client;
  state = "initializing";
  lastError = null;
  removeStaleChromiumLocks(path.join(dataDir, "auth", "session-chemsource"));
  const browserArgs = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-quic",
  ];
  if (proxyServer) browserArgs.push(`--proxy-server=${proxyServer}`);
  const instance = new Client({
    authStrategy: new LocalAuth({ clientId: "chemsource", dataPath: path.join(dataDir, "auth") }),
    deviceName: "ChemSource",
    browserName: "Chrome",
    puppeteer: {
      executablePath: process.env.CHROME_BIN || "/usr/bin/chromium",
      headless: true,
      args: browserArgs,
    },
  });
  instance.on("qr", async (qr) => {
    if (!pairingCode) state = "qr";
    account = null;
    qrDataUrl = await QRCode.toDataURL(qr, { errorCorrectionLevel: "M", margin: 2, width: 360 });
  });
  instance.on("code", (code) => {
    pairingCode = String(code || "");
    pairingCodeExpiresAt = Date.now() + 180000;
    state = "pairing_code";
    qrDataUrl = null;
  });
  instance.on("loading_screen", (percent) => {
    loadingPercent = Number.isFinite(Number(percent)) ? Number(percent) : null;
  });
  instance.on("change_state", (nextState) => {
    clientState = nextState ? String(nextState) : null;
  });
  instance.on("authenticated", () => {
    state = "authenticated";
    qrDataUrl = null;
    pairingCode = null;
    pairingCodeExpiresAt = 0;
  });
  instance.on("ready", () => {
    state = "ready";
    qrDataUrl = null;
    pairingCode = null;
    pairingCodeExpiresAt = 0;
    loadingPercent = 100;
    account = instance.info && instance.info.wid ? instance.info.wid.user : null;
    void flushEvents();
  });
  instance.on("auth_failure", () => {
    state = "auth_failure";
    lastError = "authentication_failed";
    pairingCode = null;
    pairingCodeExpiresAt = 0;
  });
  instance.on("disconnected", (reason) => {
    state = "disconnected";
    clientState = reason ? String(reason) : null;
    account = null;
    pairingCode = null;
    pairingCodeExpiresAt = 0;
  });
  instance.on("message", (message) => void queueIncoming(message));
  client = instance;
  initializing = instance.initialize().catch(async (error) => {
    if (!shouldRun) {
      state = "stopped";
    } else {
      state = "error";
      lastError = safeInitializationError(error);
    }
    if (client === instance) client = null;
    try { await instance.destroy(); } catch (_destroyError) {
      // The browser may already be closed after a failed navigation.
    }
    scheduleInitializationRetry();
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
app.use(express.json({ limit: "40mb" }));

app.get("/health", (_req, res) => res.json({ ok: true, state }));
app.use(authorize);
app.get("/status", (_req, res) => res.json(statusPayload()));
app.get("/qr", (_req, res) => {
  if (!qrDataUrl) return res.status(404).json({ detail: "qr_not_available" });
  return res.json({ qr_data_url: qrDataUrl });
});
app.post("/pairing-code", async (req, res) => {
  const phoneNumber = String(req.body.phone_number || "").replace(/\D/g, "");
  if (phoneNumber.length < 8 || phoneNumber.length > 15) {
    return res.status(422).json({ detail: "invalid_phone_number" });
  }
  if (!client || !client.pupPage || state === "ready") {
    return res.status(409).json({ detail: "whatsapp_not_waiting_for_pairing" });
  }
  try {
    const code = await client.requestPairingCode(phoneNumber, false, 600000);
    pairingCode = String(code || "");
    pairingCodeExpiresAt = Date.now() + 180000;
    state = "pairing_code";
    qrDataUrl = null;
    lastError = null;
    return res.json({ pairing_code: pairingCode, expires_in_seconds: 180 });
  } catch (_error) {
    lastError = "pairing_code_failed";
    return res.status(502).json({ detail: "pairing_code_failed" });
  }
});
app.post("/pairing-code/cancel", async (_req, res) => {
  pairingCode = null;
  pairingCodeExpiresAt = 0;
  if (client && typeof client.cancelPairingCode === "function") {
    try { await client.cancelPairingCode(); } catch (_error) {
      return res.status(502).json({ detail: "pairing_code_cancel_failed" });
    }
  }
  state = "qr";
  return res.json(statusPayload());
});
app.post("/connect", async (_req, res) => {
  try {
    shouldRun = true;
    void startClient().catch(() => {});
    return res.status(202).json(statusPayload());
  } catch (_error) {
    return res.status(503).json(statusPayload());
  }
});
app.post("/disconnect", async (_req, res) => {
  shouldRun = false;
  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  const instance = client;
  client = null;
  qrDataUrl = null;
  pairingCode = null;
  pairingCodeExpiresAt = 0;
  account = null;
  clientState = null;
  loadingPercent = null;
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
    const messageId = providerMessageId(message) || `web-out-${crypto.randomUUID()}`;
    return res.status(201).json({ message_id: messageId });
  } catch (_error) {
    return res.status(502).json({ detail: "delivery_failed" });
  }
});
app.post("/media", async (req, res) => {
  if (state !== "ready" || !client) return res.status(409).json({ detail: "whatsapp_not_ready" });
  const recipient = String(req.body.to || "").replace(/\D/g, "");
  const filename = path.basename(String(req.body.filename || "document")).slice(0, 200);
  const contentType = String(req.body.content_type || "application/octet-stream").slice(0, 200);
  const contentBase64 = String(req.body.content_base64 || "");
  const caption = String(req.body.caption || "").trim().slice(0, 1024);
  if (recipient.length < 8 || recipient.length > 15 || !contentBase64) {
    return res.status(422).json({ detail: "invalid_media" });
  }
  try {
    const registered = await client.isRegisteredUser(`${recipient}@c.us`);
    if (!registered) return res.status(422).json({ detail: "recipient_not_registered" });
    const media = new MessageMedia(contentType, contentBase64, filename || "document");
    const message = await client.sendMessage(`${recipient}@c.us`, media, { caption });
    const messageId = providerMessageId(message) || `web-out-${crypto.randomUUID()}`;
    return res.status(201).json({ message_id: messageId });
  } catch (_error) {
    return res.status(502).json({ detail: "media_delivery_failed" });
  }
});

app.listen(port, "0.0.0.0", () => {
  if (serviceToken) void startClient().catch(() => {});
});
setInterval(() => void flushEvents(), 15000).unref();
