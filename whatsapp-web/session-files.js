"use strict";

const fs = require("fs");
const path = require("path");

const STALE_CHROMIUM_LOCK_FILES = [
  "DevToolsActivePort",
  "SingletonCookie",
  "SingletonLock",
  "SingletonSocket",
];

function removeStaleChromiumLocks(sessionDirectory) {
  for (const name of STALE_CHROMIUM_LOCK_FILES) {
    try {
      fs.rmSync(path.join(sessionDirectory, name), { force: true });
    } catch (error) {
      if (!error || error.code !== "ENOENT") throw error;
    }
  }
}

module.exports = { removeStaleChromiumLocks };
