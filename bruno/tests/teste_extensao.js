// Testa a extensão com um "vscode" falso: liga o roteador de verdade, gera a config do agente e grava chaves.
// Uso: ROTEADOR_EXE=/caminho/roteador node teste_extensao.js
const Module = require("module");
const fs = require("fs");
const os = require("os");
const path = require("path");
const assert = require("assert");

const casa = fs.mkdtempSync(path.join(os.tmpdir(), "bide-"));
process.env.HOME = casa;
process.env.APPDATA = casa;
const exe = process.env.ROTEADOR_EXE;
if (!exe) { console.error("Defina ROTEADOR_EXE"); process.exit(2); }

const chamadas = { update: [], exec: [], msgs: [], cmds: {} };
const cfgs = { brunoIde: { comandoRoteador: exe, autonomia: "alta", iniciarRoteador: true }, "roo-cline": {} };
const falso = {
  workspace: { getConfiguration: (n) => ({ get: (k) => cfgs[n][k], update: async (k, v) => { cfgs[n][k] = v; chamadas.update.push([n, k, v]); } }) },
  window: {
    createOutputChannel: () => ({ appendLine() {}, append() {}, dispose() {} }),
    createStatusBarItem: () => ({ show() {}, dispose() {} }),
    showInformationMessage: async (m) => { chamadas.msgs.push(m); return undefined; },
    showWarningMessage: async () => undefined, showQuickPick: async () => undefined, showInputBox: async () => undefined,
    showTextDocument: async () => undefined,
  },
  commands: { registerCommand: (id, fn) => { chamadas.cmds[id] = fn; return { dispose() {} }; }, executeCommand: async (...a) => { chamadas.exec.push(a); } },
  extensions: { getExtension: () => undefined },
  env: { openExternal: async () => true }, Uri: { parse: (s) => s, file: (s) => s },
  StatusBarAlignment: { Left: 1 }, ThemeColor: class {}, ConfigurationTarget: { Global: 1 },
};
const orig = Module._load;
Module._load = function (req, ...r) { return req === "vscode" ? falso : orig.call(this, req, ...r); };

(async () => {
  const ext = require("../extensions/bruno-agente/extension.js");
  const ctx = { subscriptions: [], globalState: { get: () => undefined, update: async () => {} } };
  await ext.activate(ctx);
  const raiz = process.platform === "win32" ? casa : path.join(casa, ".config");
  const pasta = path.join(raiz, "BrunoIDE", "roteador");
  const arq = path.join(pasta, "agente-config.json");
  assert(fs.existsSync(arq), "config do agente não foi criada");
  const j = JSON.parse(fs.readFileSync(arq, "utf8"));
  const p = j.providerProfiles.apiConfigs.Roteador;
  assert.strictEqual(p.anthropicBaseUrl, "http://127.0.0.1:8082");
  assert(p.apiKey && p.apiKey.length > 20, "token ausente");
  assert.strictEqual(j.globalSettings.alwaysAllowExecute, true);
  assert.strictEqual(cfgs["roo-cline"].autoImportSettingsPath, arq);
  assert(chamadas.exec.some((a) => a[1] === "RooVeterinaryInc.roo-cline"), "não tentou instalar o agente");
  const r = await fetch("http://127.0.0.1:8082/", {}); assert(r.ok, "roteador não respondeu");
  const sem = await fetch("http://127.0.0.1:8082/v1/messages", { method: "POST", body: "{}" }); assert.strictEqual(sem.status, 401);
  // o token gravado precisa ser aceito pelo roteador
  const painel = await fetch("http://127.0.0.1:8082/api/painel", { headers: { "x-api-key": p.apiKey } }); assert.strictEqual(painel.status, 200);
  ext.deactivate();
  console.log("EXTENSAO OK: roteador ligou, token aceito, agente configurado e instalação solicitada");
  process.exit(0);
})().catch((e) => { console.error("FALHOU:", e); process.exit(1); });
