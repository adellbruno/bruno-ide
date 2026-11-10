// Testa só a parte nova da extensão: Git e llama.cpp embutidos (PATH, git.path, identidade, verificação).
// Usa um roteador falso e uma pasta bin/ falsa; não precisa de rede nem do build. Só Linux/macOS.
const Module = require("module");
const fs = require("fs");
const os = require("os");
const path = require("path");
const assert = require("assert");

const casa = fs.mkdtempSync(path.join(os.tmpdir(), "bide-comp-"));
process.env.HOME = casa;
const ext = path.join(casa, "ext");
const bin = path.join(ext, "bin");
fs.mkdirSync(path.join(bin, "git", "cmd"), { recursive: true });
for (const v of ["vulkan", "cpu"]) fs.mkdirSync(path.join(bin, "llama.cpp", v), { recursive: true });
const gitcfg = path.join(casa, "gitcfg.txt");
fs.writeFileSync(path.join(bin, "git", "cmd", "git"), `#!/bin/sh
CFG="${gitcfg}"; touch "$CFG"
case "$1" in
  --version) echo "git version 9.9.9.fake";;
  config) if [ "$2" = "--global" ] && [ -n "$4" ]; then echo "$3=$4" >> "$CFG"; elif [ "$2" = "--global" ]; then grep "^$3=" "$CFG" | tail -1 | cut -d= -f2-; fi;;
esac
`, { mode: 0o755 });
for (const v of ["vulkan", "cpu"]) fs.writeFileSync(path.join(bin, "llama.cpp", v, "llama-server"), "#!/bin/sh\necho 'version: fake'\n", { mode: 0o644 });
const roteador = path.join(casa, "roteador-falso.js");
fs.writeFileSync(roteador, `#!/usr/bin/env node
if (process.argv.includes("--token")) { console.log("token-falso-0123456789012345"); process.exit(0); }
require("http").createServer((q, r) => { r.statusCode = 200; r.end("ok"); }).listen(8082, "127.0.0.1");
`);

const chamadas = { update: [], msgs: [], inputs: [], prepend: [], cmds: {} };
const cfgs = { brunoIde: { comandoRoteador: "node " + roteador, autonomia: "alta", iniciarRoteador: true }, "roo-cline": {}, git: {} };
const falso = {
  workspace: { getConfiguration: (n) => ({ get: (k) => (cfgs[n] || {})[k], update: async (k, v) => { (cfgs[n] = cfgs[n] || {})[k] = v; chamadas.update.push([n, k, v]); } }) },
  window: {
    createOutputChannel: () => ({ appendLine() {}, append() {}, dispose() {} }),
    createStatusBarItem: () => ({ show() {}, dispose() {} }),
    showInformationMessage: async (m) => { chamadas.msgs.push(m); return undefined; },
    showWarningMessage: async (m) => { chamadas.msgs.push(m); },
    showQuickPick: async () => undefined,
    showInputBox: async (o) => { chamadas.inputs.push(o.prompt); return /nome/.test(o.prompt) ? "Fulano Teste" : "fulano@teste.com"; },
    showTextDocument: async () => undefined,
  },
  commands: { registerCommand: (id, fn) => { chamadas.cmds[id] = fn; return { dispose() {} }; }, executeCommand: async () => {} },
  extensions: { getExtension: () => ({}) },
  env: { openExternal: async () => true }, Uri: { parse: (s) => s, file: (s) => s },
  StatusBarAlignment: { Left: 1 }, ThemeColor: class {}, ConfigurationTarget: { Global: 1 },
};
const orig = Module._load;
Module._load = function (req, ...r) { return req === "vscode" ? falso : orig.call(this, req, ...r); };

(async () => {
  const e = require("../extensions/bruno-agente/extension.js");
  const estado = {};
  const ctx = {
    extensionPath: ext, subscriptions: [],
    globalState: { get: (k) => estado[k], update: async (k, v) => { estado[k] = v; } },
    environmentVariableCollection: { prepend: (k, v) => chamadas.prepend.push([k, v]) },
  };
  await e.activate(ctx);
  const gitExe = path.join(bin, "git", "cmd", "git");
  assert.deepStrictEqual(chamadas.prepend[0], ["PATH", path.join(bin, "git", "cmd") + path.delimiter], "PATH do terminal sem o git embutido");
  assert.strictEqual(cfgs.git.path, gitExe, "git.path não aponta para o git embutido");
  const cfg = fs.readFileSync(gitcfg, "utf8");
  assert(cfg.includes("init.defaultBranch=main"), "branch padrão não configurada");
  assert(cfg.includes("user.name=Fulano Teste") && cfg.includes("user.email=fulano@teste.com"), "identidade do git não gravada");
  assert.strictEqual(chamadas.inputs.length, 2);
  // segunda ativação: não pergunta de novo
  chamadas.inputs.length = 0;
  await e.activate({ ...ctx, subscriptions: [] });
  assert.strictEqual(chamadas.inputs.length, 0, "perguntou a identidade de novo");
  // verificação mostra git e as duas variantes do llama.cpp
  await chamadas.cmds["brunoIde.verificarComponentes"]();
  const msg = chamadas.msgs[chamadas.msgs.length - 1];
  assert(/git version 9\.9\.9\.fake \(embutido\)/.test(msg), msg);
  assert(/llama\.cpp \(vulkan\): ok/.test(msg) && /llama\.cpp \(cpu\): ok/.test(msg), msg);
  // com a opção desligada, o git embutido não entra no PATH
  cfgs.brunoIde.usarGitEmbutido = false;
  chamadas.prepend.length = 0;
  await e.activate({ ...ctx, subscriptions: [] });
  assert.strictEqual(chamadas.prepend.length, 0, "usou o git embutido com a opção desligada");
  e.deactivate();
  console.log("COMPONENTES OK: git embutido (PATH, git.path, identidade) e llama.cpp verificados");
  process.exit(0);
})().catch((x) => { console.error("FALHOU:", x); process.exit(1); });
