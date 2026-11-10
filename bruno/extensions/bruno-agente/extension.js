// Bruno IDE - liga o roteador de IA e configura o agente. JavaScript puro, sem dependências.
const vscode = require("vscode");
const cp = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const PORTA = 8082;
const AGENTE_ID = "RooVeterinaryInc.roo-cline";
const PROVEDORES = [
  { rotulo: "Google Gemini", var: "GEMINI_API_KEYS" },
  { rotulo: "Groq", var: "GROQ_API_KEYS" },
  { rotulo: "OpenRouter", var: "OPENROUTER_API_KEYS" },
  { rotulo: "DeepSeek", var: "DEEPSEEK_API_KEYS" },
  { rotulo: "NVIDIA", var: "NVIDIA_API_KEYS" },
  { rotulo: "Anthropic (Claude oficial)", var: "ANTHROPIC_REAL_API_KEYS" },
];

let processo = null;
let barra = null;
let canal = null;
let timer = null;
let tokenCache = null;
let extPath = "";

function pastaRoteador() {
  const raiz = process.platform === "win32" ? process.env.APPDATA : path.join(os.homedir(), ".config");
  return path.join(raiz || os.homedir(), "BrunoIDE", "roteador");
}

function exeRoteador() {
  const extra = vscode.workspace.getConfiguration("brunoIde").get("comandoRoteador");
  if (extra && extra.trim()) return { cmd: extra.trim(), shell: true };
  const nome = process.platform === "win32" ? "roteador.exe" : "roteador";
  const base = path.dirname(process.execPath);
  const candidatos = [
    path.join(extPath, "bin", nome),
    path.join(base, "resources", "roteador", nome),
    path.join(base, "..", "Resources", "roteador", nome), // macOS: .app/Contents/Resources
    path.join(base, "..", "resources", "roteador", nome),
  ];
  const achado = candidatos.find((c) => fs.existsSync(c));
  if (achado && process.platform !== "win32") { try { fs.chmodSync(achado, 0o755); } catch (_) {} }
  return achado ? { cmd: achado, shell: false } : null;
}

// ---- componentes embutidos no instalador: llama.cpp (modelos GGUF) e Git
function pastaBin() {
  const c = path.join(extPath, "bin");
  return fs.existsSync(c) ? c : null;
}

function pastaLlama() {
  const b = pastaBin();
  const d = b && path.join(b, "llama.cpp");
  return d && fs.existsSync(d) ? d : null;
}

function gitEmbutido() {
  const b = pastaBin();
  if (!b) return null;
  const nome = process.platform === "win32" ? "git.exe" : "git";
  const exe = path.join(b, "git", "cmd", nome);
  return fs.existsSync(exe) ? { exe, dir: path.dirname(exe) } : null;
}

function usarGitEmbutido() {
  return vscode.workspace.getConfiguration("brunoIde").get("usarGitEmbutido") !== false;
}

function ambiente() {
  const env = { ...process.env, ROTEADOR_HOME: pastaRoteador(), PYTHONUTF8: "1" };
  const llama = pastaLlama();
  if (llama) env.BRUNO_LLAMA_DIR = llama;
  const g = gitEmbutido();
  if (g && usarGitEmbutido()) {
    const chave = Object.keys(env).find((k) => k.toLowerCase() === "path") || "PATH";
    env[chave] = g.dir + path.delimiter + (env[chave] || "");
  }
  return env;
}

function gitExecutavel() {
  const g = gitEmbutido();
  return g && usarGitEmbutido() ? g.exe : "git";
}

function git(args) {
  return new Promise((ok) => {
    cp.execFile(gitExecutavel(), args, { env: ambiente(), timeout: 15000, windowsHide: true }, (e, out) => ok(e ? null : String(out).trim()));
  });
}

// Deixa o git pronto: terminais e agente enxergam o git embutido, o Git do editor usa ele, e a identidade é pedida uma vez.
async function configurarGit(ctx) {
  const g = gitEmbutido();
  if (g && usarGitEmbutido()) {
    try {
      if (ctx.environmentVariableCollection) ctx.environmentVariableCollection.prepend("PATH", g.dir + path.delimiter);
    } catch (e) { canal.appendLine("PATH do terminal: " + e); }
    try {
      await vscode.workspace.getConfiguration("git").update("path", g.exe, vscode.ConfigurationTarget.Global);
    } catch (e) { canal.appendLine("git.path: " + e); }
  }
  const versao = await git(["--version"]);
  if (!versao) {
    canal.appendLine("Git não encontrado.");
    if (!ctx.globalState.get("avisouSemGit")) {
      await ctx.globalState.update("avisouSemGit", true);
      vscode.window.showWarningMessage("Git não encontrado nesta instalação. Instale em git-scm.com para usar controle de versão.");
    }
    return;
  }
  canal.appendLine("Git: " + versao + (g && usarGitEmbutido() ? " (embutido)" : ""));
  if (!(await git(["config", "--global", "init.defaultBranch"]))) await git(["config", "--global", "init.defaultBranch", "main"]);
  const nome = await git(["config", "--global", "user.name"]);
  const email = await git(["config", "--global", "user.email"]);
  if ((!nome || !email) && !ctx.globalState.get("perguntouIdentidadeGit")) {
    await ctx.globalState.update("perguntouIdentidadeGit", true);
    const n = nome || (await vscode.window.showInputBox({ prompt: "Git: seu nome (aparece nos commits)", ignoreFocusOut: true }));
    const e = email || (await vscode.window.showInputBox({ prompt: "Git: seu e-mail (aparece nos commits)", ignoreFocusOut: true }));
    if (n && n.trim() && !nome) await git(["config", "--global", "user.name", n.trim()]);
    if (e && e.trim() && !email) await git(["config", "--global", "user.email", e.trim()]);
  }
}

function executar(exe, args) {
  return new Promise((ok) => {
    cp.execFile(exe, args, { env: ambiente(), timeout: 20000, windowsHide: true, cwd: path.dirname(exe) }, (e, out, err) => ok(e ? null : (String(out) + String(err)).trim().split(/\r?\n/)[0]));
  });
}

async function verificarComponentes() {
  const linhas = [];
  const g = await git(["--version"]);
  linhas.push("Git: " + (g ? g + (gitEmbutido() && usarGitEmbutido() ? " (embutido)" : " (do sistema)") : "não encontrado"));
  const llama = pastaLlama();
  if (!llama) {
    linhas.push("llama.cpp: não veio nesta instalação (rode 'Baixar suporte a modelos locais').");
  } else {
    const nome = process.platform === "win32" ? "llama-server.exe" : "llama-server";
    for (const v of fs.readdirSync(llama)) {
      const exe = path.join(llama, v, nome);
      if (!fs.existsSync(exe)) continue;
      if (process.platform !== "win32") { try { fs.chmodSync(exe, 0o755); } catch (_) {} }
      const r = await executar(exe, ["--version"]);
      linhas.push("llama.cpp (" + v + "): " + (r ? "ok - " + r : "presente (não abriu sem placa/driver compatível)"));
    }
  }
  const gguf = (() => { try { return fs.readdirSync(path.join(pastaRoteador(), "modelos")).filter((f) => f.toLowerCase().endsWith(".gguf")); } catch (_) { return []; } })();
  linhas.push("Modelos .gguf na pasta: " + gguf.length);
  canal.appendLine(linhas.join("\n"));
  vscode.window.showInformationMessage(linhas.join("  |  "));
}

function ping() {
  return new Promise((ok) => {
    const req = http.get({ host: "127.0.0.1", port: PORTA, path: "/", timeout: 1500 }, (res) => {
      res.resume();
      ok(res.statusCode === 200);
    });
    req.on("error", () => ok(false));
    req.on("timeout", () => { req.destroy(); ok(false); });
  });
}

function rodar(exe, args) {
  return new Promise((ok, falha) => {
    cp.execFile(exe.cmd, args, { env: ambiente(), shell: exe.shell, timeout: 20000 }, (e, out) => (e ? falha(e) : ok(String(out).trim())));
  });
}

async function obterToken(exe) {
  if (tokenCache) return tokenCache;
  const saida = await rodar(exe, ["--token"]);
  tokenCache = saida.split(/\r?\n/).pop().trim();
  return tokenCache;
}

async function iniciarRoteador(forcar) {
  const exe = exeRoteador();
  if (!exe) {
    canal.appendLine("Executável do roteador não encontrado nesta instalação.");
    atualizarBarra(false, "roteador não instalado");
    return false;
  }
  if (!forcar && (await ping())) return true;
  if (processo) { try { processo.kill(); } catch (_) {} processo = null; }
  canal.appendLine("Iniciando o roteador: " + exe.cmd);
  processo = cp.spawn(exe.cmd, [], { env: ambiente(), shell: exe.shell, windowsHide: true });
  processo.stdout.on("data", (d) => canal.append(String(d)));
  processo.stderr.on("data", (d) => canal.append(String(d)));
  processo.on("exit", (c) => { canal.appendLine("Roteador encerrou (código " + c + ")."); processo = null; });
  for (let i = 0; i < 60; i++) {
    if (await ping()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function atualizarBarra(ativo, texto) {
  barra.text = (ativo ? "$(circuit-board) IA: ativo" : "$(warning) IA: parado") + (texto ? " — " + texto : "");
  barra.tooltip = "Roteador de IA (clique para abrir o painel)";
  barra.backgroundColor = ativo ? undefined : new vscode.ThemeColor("statusBarItem.warningBackground");
}

async function monitorar() {
  const ativo = await ping();
  atualizarBarra(ativo);
  if (!ativo && vscode.workspace.getConfiguration("brunoIde").get("iniciarRoteador")) iniciarRoteador(false);
}

function perfilAutonomia(nivel) {
  const comandos = ["npm", "npx", "node", "yarn", "pnpm", "python", "python3", "pip", "pip3", "git", "dotnet", "cargo", "go",
    "pytest", "pyinstaller", "tsc", "mvn", "gradle", "make", "cmake", "ls", "dir", "cat", "type", "mkdir", "echo", "cd", "iscc"];
  const negados = ["rm -rf /", "rm -rf ~", "format ", "mkfs", "del /s /q c:\\", "shutdown", "git push --force", "git reset --hard origin"];
  const alta = nivel === "alta";
  const media = nivel === "media";
  return {
    autoApprovalEnabled: alta || media,
    alwaysAllowReadOnly: alta || media,
    alwaysAllowWrite: alta || media,
    alwaysAllowExecute: alta,
    allowedCommands: comandos,
    deniedCommands: negados,
  };
}

async function configurarAgente(avisar) {
  const exe = exeRoteador();
  if (!exe) return;
  const token = await obterToken(exe);
  const nivel = vscode.workspace.getConfiguration("brunoIde").get("autonomia") || "alta";
  const dados = {
    providerProfiles: {
      currentApiConfigName: "Roteador",
      apiConfigs: {
        Roteador: {
          id: "bruno-roteador",
          apiProvider: "anthropic",
          apiKey: token,
          anthropicBaseUrl: "http://127.0.0.1:" + PORTA,
          anthropicUseAuthToken: true,
          apiModelId: "claude-sonnet-4-5",
        },
      },
    },
    globalSettings: perfilAutonomia(nivel),
  };
  const pasta = pastaRoteador();
  fs.mkdirSync(pasta, { recursive: true });
  const arq = path.join(pasta, "agente-config.json");
  fs.writeFileSync(arq, JSON.stringify(dados, null, 2), { encoding: "utf8", mode: 0o600 });
  await vscode.workspace.getConfiguration("roo-cline").update("autoImportSettingsPath", arq, vscode.ConfigurationTarget.Global);
  if (avisar) {
    const r = await vscode.window.showInformationMessage("Agente reconfigurado. Recarregue a janela para aplicar.", "Recarregar agora");
    if (r) vscode.commands.executeCommand("workbench.action.reloadWindow");
  }
}

async function garantirAgente(ctx) {
  if (vscode.extensions.getExtension(AGENTE_ID)) return;
  try {
    await vscode.commands.executeCommand("workbench.extensions.installExtension", AGENTE_ID);
    if (!ctx.globalState.get("agenteInstaladoAviso")) {
      await ctx.globalState.update("agenteInstaladoAviso", true);
      const r = await vscode.window.showInformationMessage("Agente de programação instalado. Recarregue a janela para começar.", "Recarregar agora");
      if (r) vscode.commands.executeCommand("workbench.action.reloadWindow");
    }
  } catch (e) {
    canal.appendLine("Não consegui instalar o agente automaticamente: " + e);
    vscode.window.showWarningMessage("Não consegui instalar o agente automaticamente. Procure por 'Roo Code' na aba de extensões.");
  }
}

function gravarChave(varNome, chave) {
  const arq = path.join(pastaRoteador(), ".env");
  let linhas = fs.existsSync(arq) ? fs.readFileSync(arq, "utf8").split(/\r?\n/) : [];
  let achou = false;
  linhas = linhas.map((l) => {
    if (l.startsWith(varNome + "=")) {
      achou = true;
      const atuais = l.slice(varNome.length + 1).split(",").map((s) => s.trim()).filter(Boolean);
      if (!atuais.includes(chave)) atuais.push(chave);
      return varNome + "=" + atuais.join(",");
    }
    return l;
  });
  if (!achou) linhas.push(varNome + "=" + chave);
  fs.mkdirSync(path.dirname(arq), { recursive: true });
  fs.writeFileSync(arq, linhas.join("\n").replace(/\n*$/, "\n"), { encoding: "utf8", mode: 0o600 });
}

async function cadastrarChave() {
  const p = await vscode.window.showQuickPick(PROVEDORES.map((x) => x.rotulo), { placeHolder: "Para qual provedor é a chave?" });
  if (!p) return;
  const prov = PROVEDORES.find((x) => x.rotulo === p);
  const chave = await vscode.window.showInputBox({ prompt: "Cole a chave de API de " + p, password: true, ignoreFocusOut: true });
  if (!chave || !chave.trim()) return;
  gravarChave(prov.var, chave.trim());
  const mais = await vscode.window.showInformationMessage("Chave salva. O roteador já usa sem reiniciar. Cadastrar outra?", "Cadastrar outra", "Pronto");
  if (mais === "Cadastrar outra") return cadastrarChave();
}

async function abrirPainel() {
  const exe = exeRoteador();
  if (!exe) return vscode.window.showWarningMessage("Roteador não encontrado.");
  if (!(await ping())) await iniciarRoteador(false);
  const t = await obterToken(exe);
  vscode.env.openExternal(vscode.Uri.parse("http://127.0.0.1:" + PORTA + "/painel?t=" + encodeURIComponent(t)));
}

async function primeiraVez(ctx) {
  const temChave = (() => {
    try {
      const txt = fs.readFileSync(path.join(pastaRoteador(), ".env"), "utf8");
      return /_KEYS=\S+/.test(txt);
    } catch (_) { return false; }
  })();
  if (!temChave && !ctx.globalState.get("avisouSemChave")) {
    await ctx.globalState.update("avisouSemChave", true);
    const r = await vscode.window.showInformationMessage("Bem-vindo! Falta só cadastrar suas chaves de API (ou usar um modelo local GGUF).", "Cadastrar chave", "Depois");
    if (r === "Cadastrar chave") cadastrarChave();
  }
}

async function activate(ctx) {
  extPath = ctx.extensionPath || "";
  canal = vscode.window.createOutputChannel("Bruno IDE - Roteador");
  barra = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  barra.command = "brunoIde.abrirPainel";
  barra.show();
  atualizarBarra(false, "iniciando");
  ctx.subscriptions.push(canal, barra);

  const reg = (id, fn) => ctx.subscriptions.push(vscode.commands.registerCommand(id, fn));
  reg("brunoIde.cadastrarChave", cadastrarChave);
  reg("brunoIde.abrirPainel", abrirPainel);
  reg("brunoIde.reiniciar", async () => { atualizarBarra(false, "reiniciando"); await iniciarRoteador(true); monitorar(); });
  reg("brunoIde.configurarAgente", () => configurarAgente(true));
  reg("brunoIde.editarConfig", async () => {
    const f = path.join(pastaRoteador(), "config.yaml");
    if (!fs.existsSync(f)) await iniciarRoteador(false);
    vscode.window.showTextDocument(vscode.Uri.file(f));
  });
  reg("brunoIde.verificarComponentes", verificarComponentes);
  reg("brunoIde.instalarLlama", async () => {
    const exe = exeRoteador();
    if (!exe) return;
    if (pastaLlama()) {
      const r = await vscode.window.showInformationMessage("O llama.cpp já vem embutido no Bruno IDE. Baixar outra versão mesmo assim?", "Baixar", "Cancelar");
      if (r !== "Baixar") return;
    }
    const t = vscode.window.createTerminal({ name: "Suporte a modelos locais", env: ambiente() });
    t.show();
    t.sendText('"' + exe.cmd + '" --instalar-llama');
  });
  reg("brunoIde.pastaModelos", async () => {
    const pasta = path.join(pastaRoteador(), "modelos");
    fs.mkdirSync(pasta, { recursive: true });
    vscode.env.openExternal(vscode.Uri.file(pasta));
  });

  if (vscode.workspace.getConfiguration("brunoIde").get("iniciarRoteador")) await iniciarRoteador(false);
  try { await configurarAgente(false); } catch (e) { canal.appendLine("configurarAgente: " + e); }
  try { await configurarGit(ctx); } catch (e) { canal.appendLine("configurarGit: " + e); }
  await garantirAgente(ctx);
  await monitorar();
  timer = setInterval(monitorar, 15000);
  ctx.subscriptions.push({ dispose: () => clearInterval(timer) });
  primeiraVez(ctx);
}

function deactivate() {
  if (processo) { try { processo.kill(); } catch (_) {} }
}

module.exports = { activate, deactivate };
