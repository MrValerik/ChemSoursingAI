"use strict";

function phoneNumberFromWid(value) {
  const wid = String(value || "");
  if (!/@(c|s)\.us$/.test(wid)) return null;
  const digits = wid.replace(/@(c|s)\.us$/, "").replace(/\D/g, "");
  return digits.length >= 8 && digits.length <= 15 ? digits : null;
}

async function resolveSenderNumber(message, client) {
  const from = String(message && message.from || "");
  const classicNumber = phoneNumberFromWid(from);
  if (classicNumber) return classicNumber;

  if (from.endsWith("@lid") && client && typeof client.getContactLidAndPhone === "function") {
    try {
      const mappings = await client.getContactLidAndPhone([from]);
      const mapped = Array.isArray(mappings) ? mappings[0] : null;
      const phoneNumber = phoneNumberFromWid(mapped && mapped.pn);
      if (phoneNumber) return phoneNumber;
    } catch (_error) {
      // Older Web builds may not expose the LID mapping store yet.
    }
  }

  try {
    const contact = await message.getContact();
    const contactWid = contact && contact.id && contact.id._serialized;
    const contactNumber = phoneNumberFromWid(contactWid);
    if (contactNumber) return contactNumber;
    if (contact && contact.id && /^(c|s)\.us$/.test(String(contact.id.server || ""))) {
      const digits = String(contact.number || contact.id.user || "").replace(/\D/g, "");
      if (digits.length >= 8 && digits.length <= 15) return digits;
    }
  } catch (_error) {
    // The callback is ignored when WhatsApp cannot resolve a real phone number.
  }
  return null;
}

module.exports = { phoneNumberFromWid, resolveSenderNumber };
