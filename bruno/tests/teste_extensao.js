// Testa a extensão com um "vscode" falso: liga o roteador de verdade, gera a config do agente, monta a tela
// inicial, roda o assistente de modelo local (GGUF) e o download. Uso: ROTEADOR_EXE=/caminho/roteador node teste_extensao.js
const Module = require("module");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const assert = require("assert");

const casa = fs.mkdtempSync(path.join(os.tmpdir(), "bide-"));
process.env.HOME = casa;
process.env.USERPROFILE = casa;
process.env.APPDATA = casa;
const exe = process.env.ROTEADOR_EXE;
if (!exe) { console.error("Defina ROTEADOR_EXE"); process.exit(2); }

const chamadas = { update: [], exec: [], msgs: [], cmds: {}, provedores: {}, escolhas: [], abrir: [] };
const cfgs = { brunoIde: { comandoRoteador: exe, autonomia: "alta", iniciarRoteador: true, idiomaPortugues: true }, "roo-cline": {} };
const falso = {
  workspace: { getConfiguration: (n) => ({ get: (k) => cfgs[n][k], update: async (k, v) => { cfgs[n][k] = v; chamadas.update.push([n, k, v]); } }) },
  window: {
    createOutputChannel: () => ({ appendLine() {}, append() {}, show() {}, dispose() {} }),
    createStatusBarItem: () => ({ show() {}, dispose() {} }),
    showInformationMessage: async (m) => { chamadas.msgs.push(m); return undefined; },
    showWarningMessage: async (m) => { chamadas.msgs.push(m); return undefined; },
    showErrorMessage: async (m) => { chamadas.msgs.push(m); return undefined; },
    showQuickPick: async (itens) => {
      const proximo = chamadas.escolhas.shift();
      if (!proximo) return undefined;
      return itens.find((i) => (i.id === proximo) || (i.v === proximo));
    },
    showInputBox: async () => undefined,
    showOpenDialog: async () => [{ fsPath: chamadas.arquivoEscolhido }],
    showTextDocument: async () => undefined,
    withProgress: async (_o, fn) => fn({ report() {} }, { onCancellationRequested() {} }),
    registerWebviewViewProvider: (id, p) => { chamadas.provedores[id] = p; return { dispose() {} }; },
    createTerminal: () => ({ show() {}, sendText() {} }),
  },
  commands: { registerCommand: (id, fn) => { chamadas.cmds[id] = fn; return { dispose() {} }; }, executeCommand: async (...a) => { chamadas.exec.push(a); } },
  extensions: { getExtension: () => undefined },
  env: { openExternal: async (u) => { chamadas.abrir.push(String(u)); return true; } }, Uri: { parse: (s) => s, file: (s) => s },
  StatusBarAlignment: { Left: 1 }, ThemeColor: class {}, ConfigurationTarget: { Global: 1 },
  ProgressLocation: { Notification: 15 },
};
const orig = Module._load;
Module._load = function (req, ...r) { return req === "vscode" ? falso : orig.call(this, req, ...r); };

(async () => {
  const ext = require("../extensions/bruno-agente/extension.js");
  const estado = {};
  const ctx = { subscriptions: [], extensionPath: path.join(__dirname, "..", "extensions", "bruno-agente"),
    globalState: { get: (k) => estado[k], update: async (k, v) => { estado[k] = v; } } };
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
  assert.strictEqual(j.globalSettings.language, "pt-BR");
  assert(/português/.test(j.globalSettings.customInstructions));
  assert.strictEqual(cfgs["roo-cline"].autoImportSettingsPath, arq);
  assert(chamadas.exec.some((a) => a[1] === "RooVeterinaryInc.roo-cline"), "não tentou instalar o agente");
  console.log("ok: agente configurado em português");

  // idioma do editor
  await new Promise((r) => setTimeout(r, 300));
  const argv = fs.readFileSync(path.join(casa, ".brunoide", "argv.json"), "utf8");
  assert(/"locale":\s*"pt-br"/.test(argv), "argv.json sem locale");
  assert(chamadas.exec.some((a) => a[1] === "MS-CEINTL.vscode-language-pack-pt-br"), "não pediu o pacote de idioma");
  assert(chamadas.exec.some((a) => a[0] === "workbench.view.extension.bruno-ide"), "não abriu a tela inicial");
  console.log("ok: idioma português e tela inicial na primeira abertura");

  // roteador de verdade
  assert((await fetch("http://127.0.0.1:8082/")).ok, "roteador não respondeu");
  assert.strictEqual((await fetch("http://127.0.0.1:8082/v1/messages", { method: "POST", body: "{}" })).status, 401);
  assert.strictEqual((await fetch("http://127.0.0.1:8082/api/painel", { headers: { "x-api-key": p.apiKey } })).status, 200);
  console.log("ok: roteador ligado e token aceito");

  // tela inicial (webview)
  const mensagens = []; let recebe = null;
  const view = { webview: { options: null, html: "", postMessage: async (m) => { mensagens.push(m); }, onDidReceiveMessage: (f) => { recebe = f; } }, onDidDispose() {} };
  chamadas.provedores["brunoIde.inicio"].resolveWebviewView(view);
  for (const t of ["Conversar com o agente", "Cadastrar chave de API", "Instalar modelo local (GGUF)", "Painel de consumo", "Começar um projeto novo", "Como pedir"])
    assert(view.webview.html.includes(t), "tela inicial sem: " + t);
  await recebe({ cmd: "pronto" });
  const st = mensagens.find((m) => m.tipo === "status");
  assert(st && st.roteador === true && st.chaves === 0 && st.local === false, JSON.stringify(st));
  await recebe({ cmd: "gguf" });
  assert(chamadas.exec.some((a) => a[0] === "brunoIde.modeloLocal"), "botão GGUF não chamou o assistente");
  await recebe({ cmd: "agente" });
  assert(chamadas.exec.some((a) => a[0] === "brunoIde.agente"));
  console.log("ok: tela inicial em português com os botões e o status");

  // chave cadastrada pela extensão aparece no status
  fs.appendFileSync(path.join(pasta, ".env"), "GEMINI_API_KEYS=abc,def\n");
  assert.strictEqual(ext._teste.contarChaves(), 2);

  // assistente de modelo local: "usar arquivo que já tenho"
  const fonte = path.join(casa, "meu-modelo.gguf"); fs.writeFileSync(fonte, Buffer.alloc(2048, 1));
  chamadas.arquivoEscolhido = fonte;
  const falsoMotor = path.join(casa, process.platform === "win32" ? "llama-server.exe" : "llama-server");
  fs.writeFileSync(falsoMotor, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  const cfgYaml = path.join(pasta, "config.yaml");
  fs.writeFileSync(cfgYaml, fs.readFileSync(cfgYaml, "utf8").replace(/^(\s+servidor:\s*)""/m, '$1"' + falsoMotor.replace(/\\/g, "/") + '"'));
  chamadas.escolhas = ["arquivo", "so_privado"];
  await chamadas.cmds["brunoIde.modeloLocal"]();
  assert(fs.existsSync(path.join(pasta, "modelos", "meu-modelo.gguf")), "modelo não foi copiado");
  const yaml = fs.readFileSync(cfgYaml, "utf8");
  assert(/^local:\s*\n(?:.*\n)*?\s+ativo:\s*true/m.test(yaml), "modelo local não foi ligado");
  assert(/^\s+uso:\s*so_privado/m.test(yaml), "uso do modelo local não foi gravado");
  assert((await fetch("http://127.0.0.1:8082/")).ok, "roteador não voltou depois de reiniciar");
  assert.strictEqual(ext._teste.localLigado(), true);
  console.log("ok: assistente GGUF copiou o modelo, ligou o modo local e reiniciou o roteador");

  // download com redirecionamento
  const alvo = Buffer.alloc(3 * 1024 * 1024, 7);
  const srv = http.createServer((req, res) => {
    if (req.url === "/a") { res.writeHead(302, { Location: "/b" }); return res.end(); }
    res.writeHead(200, { "Content-Length": alvo.length }); res.end(alvo);
  }).listen(0);
  await new Promise((r) => srv.on("listening", r));
  const dest = path.join(casa, "baixado.gguf"); let ultimo = 0;
  await ext._teste.baixarArquivo("http://127.0.0.1:" + srv.address().port + "/a", dest, (l) => { ultimo = l; }, { onCancellationRequested() {} });
  assert.strictEqual(fs.statSync(dest).size, alvo.length); assert.strictEqual(ultimo, alvo.length);
  assert(!fs.existsSync(dest + ".parte"));
  srv.close();
  console.log("ok: download com redirecionamento");

  ext.deactivate();
  console.log("EXTENSAO OK");
  process.exit(0);
})().catch((e) => { console.error("FALHOU:", e); process.exit(1); });
