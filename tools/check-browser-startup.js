#!/usr/bin/env node
"use strict";

const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("src/gov_webui/static/index.html", "utf8");
const parts = html.split("<script>");
if (parts.length < 2) {
  throw new Error("inline script not found");
}
const script = parts[1].split("</script>")[0];

// Parse the complete browser program first.
new Function(script);

const startupProbe = script.replace(
  /\n\s*start\(\);\s*$/,
  "\nwireEvents();",
);
if (startupProbe === script) {
  throw new Error("browser start call not found");
}

const elements = new Map();
function elementFor(selector) {
  if (elements.has(selector)) return elements.get(selector);
  const listeners = new Map();
  const element = {
    addEventListener(type, handler) { listeners.set(type, handler); },
    listeners,
  };
  elements.set(selector, element);
  return element;
}

const stored = new Map();
const storage = {
  getItem(key) { return stored.get(key) ?? null; },
  setItem(key, value) { stored.set(key, value); },
  removeItem(key) { stored.delete(key); },
};

vm.runInNewContext(startupProbe, {
  clearTimeout() {},
  console,
  document: {
    addEventListener() {},
    body: elementFor("body"),
    querySelector(selector) { return elementFor(selector); },
    querySelectorAll() { return []; },
  },
  fetch() {
    throw new Error("startup probe must not call the network");
  },
  localStorage: storage,
  setTimeout() { return 0; },
  window: {localStorage: storage},
}, {filename: "src/gov_webui/static/index.html"});

const input = elementFor("#prompt").listeners.get("input");
if (typeof input !== "function") {
  throw new Error("prompt input listener not registered");
}
const target = {value: "Preserve this prompt", style: {}, scrollHeight: 20};
input({target});
if (![...stored.values()].includes(target.value)) {
  throw new Error("prompt input listener did not persist the draft");
}
