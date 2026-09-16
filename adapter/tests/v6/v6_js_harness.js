// Runs app/static/v6.js against a minimal fake DOM (test helper, not shipped).
// argv: <script path> <overview json path> <halt status>
'use strict';

const fs = require('fs');
const vm = require('vm');

const [scriptPath, overviewPath, haltStatus] = process.argv.slice(2);
const overview = JSON.parse(fs.readFileSync(overviewPath, 'utf8'));

function Node() {}
function fakeElement(tag) {
  const node = Object.create(Node.prototype);
  Object.assign(node, {
    tagName: tag, children: [], textContent: '', className: '', hidden: false,
    disabled: false, dataset: {}, style: {}, listeners: {},
  });
  node.append = (...kids) => node.children.push(...kids);
  node.replaceChildren = (...kids) => { node.children = kids; };
  node.addEventListener = (type, handler) => { node.listeners[type] = handler; };
  return node;
}

const elements = new Map();
const byId = (id) => {
  if (!elements.has(id)) elements.set(id, fakeElement('div'));
  return elements.get(id);
};
byId('v6-root').dataset = { pollMs: '5000', csrf: 'nonce-from-the-old-page' };

const calls = [];
async function fetchStub(url, options = {}) {
  calls.push({ url, headers: options.headers || {} });
  if (url === '/v6/api/overview') {
    return { status: 200, ok: true, json: async () => overview };
  }
  const status = Number(haltStatus);
  const body = status === 200 ? { created: true } : { detail: 'invalid CSRF token' };
  return { status, ok: status === 200, json: async () => body };
}

const context = vm.createContext({
  Node, fetch: fetchStub, JSON, Number, String, Object, Array, Math, Date, Set, Error,
  document: { getElementById: byId, createElement: fakeElement, hidden: false,
              addEventListener: () => undefined },
  window: { confirm: () => true, setInterval: () => 0 },
});

const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

(async () => {
  vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context);
  await settle();
  await byId('v6-halt').listeners.click();
  await settle();
  const halt = calls.find((call) => call.url === '/v6/control/halt');
  process.stdout.write(JSON.stringify({
    haltNonce: halt ? halt.headers['X-V6-CSRF'] : null,
    haltResult: byId('v6-halt-result').textContent,
    status: byId('v6-status').textContent,
    error: byId('v6-error').textContent,
  }));
})().catch((error) => {
  process.stderr.write(String(error && error.stack));
  process.exit(1);
});
