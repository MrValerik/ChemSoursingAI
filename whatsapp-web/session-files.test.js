"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { removeStaleChromiumLocks } = require("./session-files");

test("only stale Chromium lock files are removed from the saved session", (context) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "chemsource-session-"));
  context.after(() => fs.rmSync(directory, { recursive: true, force: true }));

  const lockFiles = [
    "DevToolsActivePort",
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
  ];
  for (const name of lockFiles) fs.writeFileSync(path.join(directory, name), "stale");
  fs.writeFileSync(path.join(directory, "Local State"), "saved-authentication");

  removeStaleChromiumLocks(directory);

  for (const name of lockFiles) {
    assert.equal(fs.existsSync(path.join(directory, name)), false);
  }
  assert.equal(
    fs.readFileSync(path.join(directory, "Local State"), "utf8"),
    "saved-authentication",
  );
});
