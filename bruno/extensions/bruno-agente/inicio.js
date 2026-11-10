// Tela inicial do Bruno IDE (painel lateral, tudo em português).
module.exports = function html(nonce) {
  return `<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 10px 12px 24px; margin: 0; }
  h1 { font-size: 15px; margin: 4px 0 2px; }
  p.sub { margin: 0 0 12px; opacity: .8; font-size: 12px; line-height: 1.4; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
  .chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
  .chip.ok::before { content: "● "; color: #3fb950; } .chip.ruim::before { content: "● "; color: #f0883e; }
  h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; opacity: .7; margin: 16px 0 6px; }
  button.card { display: flex; gap: 10px; align-items: flex-start; width: 100%; text-align: left; cursor: pointer;
    background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground);
    border: 1px solid var(--vscode-widget-border, transparent); border-radius: 6px; padding: 9px 10px; margin-bottom: 6px; }
  button.card:hover { background: var(--vscode-button-secondaryHoverBackground); }
  button.card.principal { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  button.card.principal:hover { background: var(--vscode-button-hoverBackground); }
  .ic { font-size: 18px; line-height: 1.1; } .t { font-weight: 600; font-size: 13px; } .d { font-size: 11.5px; opacity: .85; margin-top: 2px; line-height: 1.35; }
  .dica { font-size: 12px; line-height: 1.5; background: var(--vscode-textBlockQuote-background); border-left: 3px solid var(--vscode-textBlockQuote-border);
    padding: 8px 10px; border-radius: 0 4px 4px 0; margin-top: 6px; }
  .dica code { font-family: var(--vscode-editor-font-family); }
  ul { margin: 4px 0 0 16px; padding: 0; } li { margin: 2px 0; }
</style></head>
<body>
  <h1>Bruno IDE</h1>
  <p class="sub">Descreva em português o que você quer. O agente cria, edita, testa e corrige o código por você.</p>
  <div class="chips">
    <span id="chRot" class="chip">roteador…</span>
    <span id="chChaves" class="chip">chaves…</span>
    <span id="chLocal" class="chip">modelo local…</span>
  </div>

  <h2>Programar com linguagem natural</h2>
  <button class="card principal" data-cmd="agente">
    <span class="ic">💬</span><span><div class="t">Conversar com o agente</div>
    <div class="d">Peça em português: criar um programa, mudar um arquivo, achar um erro, explicar o código.</div></span></button>
  <button class="card" data-cmd="novoProjeto">
    <span class="ic">📁</span><span><div class="t">Começar um projeto novo</div>
    <div class="d">Escolha uma pasta vazia e descreva o que quer construir.</div></span></button>
  <button class="card" data-cmd="explorador">
    <span class="ic">📝</span><span><div class="t">Ver e editar meus arquivos</div>
    <div class="d">Abre a lista de arquivos do projeto. O agente edita; você também pode editar à mão.</div></span></button>

  <h2>Inteligência artificial</h2>
  <button class="card" data-cmd="chave">
    <span class="ic">🔑</span><span><div class="t">Cadastrar chave de API</div>
    <div class="d">Gemini, Groq, OpenRouter, DeepSeek, NVIDIA ou Claude. Pode cadastrar várias: ele troca sozinho quando uma acaba.</div></span></button>
  <button class="card" data-cmd="gguf">
    <span class="ic">🧠</span><span><div class="t">Instalar modelo local (GGUF)</div>
    <div class="d">Rode a IA no seu computador, sem internet e sem gastar cota. O assistente escolhe o que cabe na sua máquina.</div></span></button>
  <button class="card" data-cmd="painel">
    <span class="ic">📊</span><span><div class="t">Painel de consumo</div>
    <div class="d">Veja quanto cada chave já usou e qual IA está respondendo.</div></span></button>
  <button class="card" data-cmd="config">
    <span class="ic">⚙️</span><span><div class="t">Configurações avançadas</div>
    <div class="d">Ordem dos modelos, limites de gasto, privacidade.</div></span></button>

  <h2>Como pedir</h2>
  <div class="dica">
    No painel do agente, escolha o modo e escreva normalmente:
    <ul>
      <li><b>Perguntar</b>: só conversa e explica, não mexe em nada.</li>
      <li><b>Código</b>: cria e edita arquivos, roda comandos.</li>
      <li><b>Arquiteto</b>: planeja antes de programar.</li>
      <li><b>Depurar</b>: acha e corrige erros.</li>
    </ul>
    <br>Exemplo: <code>Crie um programa com janela que leia uma planilha e gere um relatório em PDF. Teste e gere o executável.</code>
  </div>

<script nonce="${nonce}">
  const vs = acquireVsCodeApi();
  document.querySelectorAll('button.card').forEach(b => b.addEventListener('click', () => vs.postMessage({ cmd: b.dataset.cmd })));
  const set = (id, ok, txt) => { const e = document.getElementById(id); e.textContent = txt; e.className = 'chip ' + (ok ? 'ok' : 'ruim'); };
  window.addEventListener('message', ev => {
    const s = ev.data; if (!s || s.tipo !== 'status') return;
    set('chRot', s.roteador, s.roteador ? 'roteador ativo' : 'roteador parado');
    set('chChaves', s.chaves > 0, s.chaves > 0 ? s.chaves + ' chave(s)' : 'sem chaves');
    set('chLocal', s.local, s.local ? 'modelo local ligado' : 'modelo local desligado');
  });
  vs.postMessage({ cmd: 'pronto' });
</script></body></html>`;
};
