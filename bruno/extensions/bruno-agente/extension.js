// Bruno IDE - liga o roteador de IA e configura o agente. JavaScript puro, sem dependências.
const vscode = require("vscode");
const cp = require("child_process");
const fs = require("fs");
const os = require("os");
const https = require("https");
const http = require("http");
const path = require("path");
const htmlInicio = require("./inicio.js");

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
let viewInicio = null;

const MODELOS = [
  { bi: 1.5, nome: "Qwen2.5-Coder 1.5B (muito leve, só tarefas simples)", gb: 1.1, arq: "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf", repo: "bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF" },
  { bi: 3, nome: "Qwen2.5-Coder 3B (leve)", gb: 2.1, arq: "Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf", repo: "bartowski/Qwen2.5-Coder-3B-Instruct-GGUF" },
  { bi: 7, nome: "Qwen2.5-Coder 7B (equilibrado, recomendado)", gb: 4.7, arq: "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf", repo: "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF" },
  { bi: 14, nome: "Qwen2.5-Coder 14B (mais capaz, pede máquina forte)", gb: 9.0, arq: "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf", repo: "bartowski/Qwen2.5-Coder-14B-Instruct-GGUF" },
];

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

function ambiente() {
  return { ...process.env, ROTEADOR_HOME: pastaRoteador(), PYTHONUTF8: "1" };
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
  enviarStatus();
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
    globalSettings: {
      ...perfilAutonomia(nivel),
      language: "pt-BR",
      customInstructions:
        "Responda sempre em português do Brasil, em linguagem simples, sem jargão desnecessário. " +
        "Ao terminar uma tarefa, diga em poucas linhas o que foi feito e como usar. " +
        "Quando criar um programa, teste-o antes de dizer que está pronto e ofereça gerar o executável ou instalador.",
    },
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

// ------------------------------------------------------------------ tela inicial
function contarChaves() {
  try {
    const txt = fs.readFileSync(path.join(pastaRoteador(), ".env"), "utf8");
    let n = 0;
    for (const l of txt.split(/\r?\n/)) {
      const m = l.match(/^[A-Z_]+_KEYS=(.*)$/);
      if (m) n += m[1].split(",").map((x) => x.trim()).filter(Boolean).length;
    }
    return n;
  } catch (_) { return 0; }
}

function localLigado() {
  try {
    const txt = fs.readFileSync(path.join(pastaRoteador(), "config.yaml"), "utf8");
    const m = txt.match(/^local:\s*(?:#.*)?\n([\s\S]*?)^\S/m);
    return !!(m && /^\s+ativo:\s*true/m.test(m[1]));
  } catch (_) { return false; }
}

async function enviarStatus() {
  if (!viewInicio) return;
  viewInicio.webview.postMessage({ tipo: "status", roteador: await ping(), chaves: contarChaves(), local: localLigado() });
}

function criarViewInicio(ctx) {
  const provedor = {
    resolveWebviewView(view) {
      viewInicio = view;
      view.webview.options = { enableScripts: true };
      const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
      view.webview.html = htmlInicio(nonce);
      view.webview.onDidReceiveMessage(async (m) => {
        const mapa = {
          agente: "brunoIde.agente", chave: "brunoIde.cadastrarChave", gguf: "brunoIde.modeloLocal",
          painel: "brunoIde.abrirPainel", config: "brunoIde.editarConfig",
        };
        if (m.cmd === "pronto") return enviarStatus();
        if (m.cmd === "novoProjeto") return vscode.commands.executeCommand("workbench.action.files.openFolder");
        if (m.cmd === "explorador") return vscode.commands.executeCommand("workbench.view.explorer");
        if (mapa[m.cmd]) return vscode.commands.executeCommand(mapa[m.cmd]);
      });
      view.onDidDispose(() => { if (viewInicio === view) viewInicio = null; });
    },
  };
  ctx.subscriptions.push(vscode.window.registerWebviewViewProvider("brunoIde.inicio", provedor));
}

async function abrirAgente() {
  if (!vscode.extensions.getExtension(AGENTE_ID)) {
    const r = await vscode.window.showWarningMessage("O agente ainda não está instalado. Instalar agora?", "Instalar", "Cancelar");
    if (r === "Instalar") await vscode.commands.executeCommand("workbench.extensions.installExtension", AGENTE_ID);
    return;
  }
  await vscode.commands.executeCommand("workbench.view.extension.roo-cline-ActivityBar");
}

// ------------------------------------------------------------------ modelo local (GGUF)
function baixarArquivo(url, destino, progresso, cancelar, redirecoes = 0) {
  return new Promise((ok, falha) => {
    if (redirecoes > 6) return falha(new Error("redirecionamentos demais"));
    const req = (url.startsWith("http:") ? http : https).get(url, { headers: { "User-Agent": "BrunoIDE" } }, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
        res.resume();
        return baixarArquivo(new URL(res.headers.location, url).toString(), destino, progresso, cancelar, redirecoes + 1).then(ok, falha);
      }
      if (res.statusCode !== 200) { res.resume(); return falha(new Error("HTTP " + res.statusCode)); }
      const total = Number(res.headers["content-length"] || 0);
      let lido = 0;
      const parcial = destino + ".parte";
      const saida = fs.createWriteStream(parcial);
      cancelar.onCancellationRequested(() => { req.destroy(new Error("cancelado")); });
      res.on("data", (c) => { lido += c.length; progresso(lido, total); });
      res.pipe(saida);
      saida.on("finish", () => { try { fs.renameSync(parcial, destino); ok(); } catch (e) { falha(e); } });
      res.on("error", falha); saida.on("error", falha);
    });
    req.on("error", (e) => { try { fs.unlinkSync(destino + ".parte"); } catch (_) {} falha(e); });
  });
}

function rodarComSaida(exe, args) {
  return new Promise((ok) => {
    const p = cp.spawn(exe.cmd, args, { env: ambiente(), shell: exe.shell, windowsHide: true });
    let saida = "";
    p.stdout.on("data", (d) => { saida += d; canal.append(String(d)); });
    p.stderr.on("data", (d) => { saida += d; canal.append(String(d)); });
    p.on("error", () => ok({ codigo: -1, saida }));
    p.on("close", (c) => ok({ codigo: c, saida }));
  });
}

async function infoLocal(exe) {
  try { return JSON.parse(await rodar(exe, ["--info-local"])); } catch (e) { canal.appendLine("info-local: " + e); return null; }
}

async function garantirMotor(exe, info) {
  if (info && info.servidor) return true;
  const r = await vscode.window.showInformationMessage(
    "Falta o \"motor\" que executa modelos GGUF (llama-server, programa gratuito e de código aberto). Baixar agora? (cerca de 30 a 200 MB)",
    "Baixar motor", "Cancelar");
  if (r !== "Baixar motor") return false;
  canal.show(true);
  const res = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Baixando o motor de modelos locais..." },
    () => rodarComSaida(exe, ["--instalar-llama"]));
  const novo = await infoLocal(exe);
  if (novo && novo.servidor) { vscode.window.showInformationMessage("Motor instalado."); return true; }
  const o = await vscode.window.showErrorMessage(
    "Não consegui instalar o motor automaticamente. Baixe o pacote do seu sistema em github.com/ggml-org/llama.cpp/releases e extraia na pasta do roteador.",
    "Abrir página de downloads", "Ver detalhes");
  if (o === "Abrir página de downloads") vscode.env.openExternal(vscode.Uri.parse("https://github.com/ggml-org/llama.cpp/releases"));
  if (o === "Ver detalhes") canal.show(true);
  return false;
}

async function ativarLocal(exe) {
  await rodar(exe, ["--local", "on"]);
  const uso = await vscode.window.showQuickPick([
    { label: "Só quando a internet ou as chaves falharem", description: "reserva (recomendado)", v: "reserva" },
    { label: "Sempre que possível, antes da nuvem", description: "principal", v: "principal" },
    { label: "Só para dados sensíveis (CPF, processos...)", description: "privado", v: "so_privado" },
  ], { placeHolder: "Quando usar o modelo local?" });
  if (uso) await rodar(exe, ["--local-uso", uso.v]);
  await iniciarRoteador(true);
  enviarStatus();
}

async function modeloLocal() {
  const exe = exeRoteador();
  if (!exe) return vscode.window.showWarningMessage("Roteador não encontrado.");
  const info = await infoLocal(exe);
  if (!info) return vscode.window.showErrorMessage("Não consegui ler o estado do modelo local. Veja o painel 'Bruno IDE - Roteador'.");
  const resumo = info.modelos.length ? info.modelos.length + " modelo(s) instalado(s)" : "nenhum modelo instalado";
  const itens = [
    { label: "$(cloud-download) Baixar um modelo recomendado para o meu computador", detail: info.recomendacao.texto, id: "baixar" },
    { label: "$(file-add) Usar um arquivo .gguf que eu já tenho", detail: "Você escolhe o arquivo e ele é copiado para a pasta de modelos.", id: "arquivo" },
    { label: info.servidor ? "$(tools) Reinstalar o motor (llama-server)" : "$(tools) Instalar o motor (llama-server) - necessário", detail: info.servidor || "Programa que executa os modelos. Gratuito.", id: "motor" },
    { label: info.ativo ? "$(circle-slash) Desligar o modelo local" : "$(play) Ligar o modelo local", detail: "Agora: " + (info.ativo ? "ligado" : "desligado") + " | " + resumo, id: "alternar" },
    { label: "$(folder-opened) Abrir a pasta de modelos", detail: info.pasta_modelos, id: "pasta" },
  ];
  const esc = await vscode.window.showQuickPick(itens, { placeHolder: "Modelo local (GGUF): o que você quer fazer?", matchOnDetail: true });
  if (!esc) return;

  if (esc.id === "pasta") { fs.mkdirSync(info.pasta_modelos, { recursive: true }); return vscode.env.openExternal(vscode.Uri.file(info.pasta_modelos)); }
  if (esc.id === "motor") { await garantirMotor(exe, { servidor: null }); return; }
  if (esc.id === "alternar") {
    if (!info.ativo && !info.modelos.length) return vscode.window.showWarningMessage("Instale um modelo primeiro (opção 'Baixar um modelo recomendado').");
    await rodar(exe, ["--local", info.ativo ? "off" : "on"]);
    await iniciarRoteador(true); enviarStatus();
    return vscode.window.showInformationMessage("Modelo local " + (info.ativo ? "desligado." : "ligado."));
  }

  fs.mkdirSync(info.pasta_modelos, { recursive: true });
  if (esc.id === "arquivo") {
    const f = await vscode.window.showOpenDialog({ canSelectMany: false, filters: { "Modelos GGUF": ["gguf"] }, openLabel: "Usar este modelo" });
    if (!f || !f[0]) return;
    const destino = path.join(info.pasta_modelos, path.basename(f[0].fsPath));
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Copiando o modelo..." },
      async () => { await fs.promises.copyFile(f[0].fsPath, destino); });
  } else {
    const maximo = info.recomendacao.bilhoes || 3;
    let lista = MODELOS.filter((m) => m.bi <= maximo);
    if (!lista.length) lista = [MODELOS[0]];
    const melhor = lista[lista.length - 1];
    const m = await vscode.window.showQuickPick(lista.map((x) => ({
      label: x.nome + (x === melhor ? "  (indicado para você)" : ""),
      detail: "Download de cerca de " + x.gb + " GB. Exige ~" + (x.gb + 1.5).toFixed(0) + " GB de memória livre.", x })),
      { placeHolder: "Qual modelo baixar? " + info.recomendacao.texto });
    if (!m) return;
    const x = m.x;
    const destino = path.join(info.pasta_modelos, x.arq);
    const url = "https://huggingface.co/" + x.repo + "/resolve/main/" + x.arq;
    if (!fs.existsSync(destino)) {
      try {
        await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Baixando " + x.nome, cancellable: true },
          async (prog, cancelar) => {
            let ultimo = 0;
            await baixarArquivo(url, destino, (lido, total) => {
              const pct = total ? Math.floor((lido / total) * 100) : 0;
              if (pct > ultimo) { prog.report({ increment: pct - ultimo, message: pct + "% (" + (lido / 1e9).toFixed(2) + " GB)" }); ultimo = pct; }
            }, cancelar);
          });
      } catch (e) {
        canal.appendLine("download: " + e);
        const o = await vscode.window.showErrorMessage("Não consegui baixar o modelo (" + e.message + "). Você pode baixar o arquivo pelo navegador e usar a opção 'Usar um arquivo .gguf que eu já tenho'.",
          "Abrir página do modelo");
        if (o) vscode.env.openExternal(vscode.Uri.parse("https://huggingface.co/" + x.repo));
        return;
      }
    }
  }
  const info2 = await infoLocal(exe);
  if (!(await garantirMotor(exe, info2))) {
    vscode.window.showWarningMessage("O modelo foi salvo, mas ainda falta o motor. Rode de novo 'Modelo local (GGUF)' e escolha 'Instalar o motor'.");
    return;
  }
  await ativarLocal(exe);
  vscode.window.showInformationMessage("Modelo local pronto! Ele será usado conforme você escolheu. Dá para mudar a qualquer momento nesta mesma opção.");
}

// ------------------------------------------------------------------ idioma português
function garantirIdioma(ctx) {
  const cfg = vscode.workspace.getConfiguration("brunoIde");
  if (!cfg.get("idiomaPortugues") || ctx.globalState.get("idiomaConfigurado")) return;
  try {
    const arq = path.join(os.homedir(), ".brunoide", "argv.json");
    fs.mkdirSync(path.dirname(arq), { recursive: true });
    let txt = fs.existsSync(arq) ? fs.readFileSync(arq, "utf8") : "";
    if (!/^\s*"locale"\s*:/m.test(txt)) {
      txt = txt.trim() ? txt.replace("{", '{\n\t"locale": "pt-br",') : '{\n\t"locale": "pt-br"\n}\n';
      fs.writeFileSync(arq, txt, "utf8");
    }
  } catch (e) { canal.appendLine("idioma: " + e); }
  const id = "MS-CEINTL.vscode-language-pack-pt-br";
  Promise.resolve(vscode.extensions.getExtension(id) ? null : vscode.commands.executeCommand("workbench.extensions.installExtension", id))
    .then(async () => {
      await ctx.globalState.update("idiomaConfigurado", true);
      const r = await vscode.window.showInformationMessage("Idioma português instalado. Recarregue para ver o editor em português.", "Recarregar agora");
      if (r) vscode.commands.executeCommand("workbench.action.reloadWindow");
    })
    .catch((e) => canal.appendLine("pacote de idioma: " + e));
}

async function activate(ctx) {
  // Durante o build o instalador abre o editor só para gerar um cache; nesse modo não ligamos nada
  // (um processo filho vivo trava a limpeza de arquivos temporários no Windows).
  if (process.env.VSCODE_GENERATE_NODE_COMPILE_CACHE) return;
  extPath = ctx.extensionPath || "";
  canal = vscode.window.createOutputChannel("Bruno IDE - Roteador");
  barra = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  barra.command = "brunoIde.abrirPainel";
  barra.show();
  atualizarBarra(false, "iniciando");
  ctx.subscriptions.push(canal, barra);

  const reg = (id, fn) => ctx.subscriptions.push(vscode.commands.registerCommand(id, fn));
  criarViewInicio(ctx);
  reg("brunoIde.inicio", () => vscode.commands.executeCommand("workbench.view.extension.bruno-ide"));
  reg("brunoIde.agente", abrirAgente);
  reg("brunoIde.modeloLocal", modeloLocal);
  reg("brunoIde.cadastrarChave", async () => { await cadastrarChave(); enviarStatus(); });
  reg("brunoIde.abrirPainel", abrirPainel);
  reg("brunoIde.reiniciar", async () => { atualizarBarra(false, "reiniciando"); await iniciarRoteador(true); monitorar(); });
  reg("brunoIde.configurarAgente", () => configurarAgente(true));
  reg("brunoIde.editarConfig", async () => {
    const f = path.join(pastaRoteador(), "config.yaml");
    if (!fs.existsSync(f)) await iniciarRoteador(false);
    vscode.window.showTextDocument(vscode.Uri.file(f));
  });
  reg("brunoIde.instalarLlama", () => {
    const exe = exeRoteador();
    if (!exe) return;
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
  await garantirAgente(ctx);
  await monitorar();
  timer = setInterval(monitorar, 15000);
  ctx.subscriptions.push({ dispose: () => clearInterval(timer) });
  garantirIdioma(ctx);
  if (!ctx.globalState.get("inicioAberto")) {
    await ctx.globalState.update("inicioAberto", true);
    vscode.commands.executeCommand("workbench.view.extension.bruno-ide");
  }
  primeiraVez(ctx);
}

function deactivate() {
  if (processo) { try { processo.kill(); } catch (_) {} }
}

module.exports = { activate, deactivate, _teste: { baixarArquivo, contarChaves, localLigado } };
