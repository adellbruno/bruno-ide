# Bruno IDE

Um editor de código (fork do VS Code, compilado pelos scripts do VSCodium, sem telemetria da Microsoft) com um **desenvolvedor de IA autônomo** já embutido:

- **Agente autônomo** (Roo Code, código aberto): você descreve em português o que quer; ele cria o projeto, escreve e edita código, roda comandos, testa, corrige erros, instala dependências, refatora e gera o executável.
- **llama.cpp e Git embutidos:** o instalador já traz o motor de modelos GGUF (llama-server, com CUDA para NVIDIA e Vulkan para AMD/Intel) e, no Windows, o Git. Nada para instalar à parte.
- **Roteador de IA** (embutido, liga sozinho): vários provedores (Gemini, Groq, OpenRouter, DeepSeek, NVIDIA, Claude oficial) e **modelos locais GGUF**. Quando uma chave acaba o limite/crédito ou dá erro, troca para a próxima automaticamente, na ordem de prioridade do `config.yaml`.
- **Privacidade:** dados sensíveis (CNJ, CPF, CNPJ, conta bancária, telefone) ficam no modelo local.

Comece pelo **GUIA_PASSO_A_PASSO.md**. Visão técnica no fim deste arquivo.

## Estrutura
| Pasta/arquivo | O que é |
|---|---|
| `GUIA_PASSO_A_PASSO.md` | como gerar o instalador e usar (linguagem simples) |
| `.github/workflows/compilar-windows.yml` | compila o editor e gera o instalador do Windows na nuvem |
| `.github/workflows/compilar-linux.yml` | idem para Linux (x64) |
| `product.json` | nome, identidade e IDs de instalação do produto |
| `bruno/extensions/bruno-agente/` | extensão embutida: liga o roteador, cadastra chaves, configura o agente, barra de status |
| `bruno/fetch_binarios.sh` | no build, baixa o llama.cpp e o MinGit e os coloca em `bruno/extensions/bruno-agente/bin/` |
| `roteador/` | o roteador de IA (Python; vira um executável único no build) |
| `patches/`, `prepare_vscode.sh` etc. | base do VSCodium (patches que tiram telemetria e ajustam o VS Code) |

## Como funciona
1. O workflow compila o roteador com PyInstaller e o coloca dentro da extensão `bruno-agente`.
2. O passo "Embutir llama.cpp e Git" baixa as versões mais recentes do llama.cpp (pastas `llama.cpp/cuda`, `vulkan`, `cpu`) e do MinGit (`git/`) para a mesma pasta `bin/`. Para fixar versões: variáveis `LLAMA_TAG` e `GIT_TAG` no script.
3. O VSCodium baixa o código do VS Code, aplica os patches, copia `bruno/extensions/*` para `extensions/` (hook em `prepare_vscode.sh`) e compila.
4. Ao abrir o editor, a extensão põe o Git embutido no PATH dos terminais e no `git.path` do editor, pergunta nome e e-mail para o Git (uma vez) e inicia o roteador (`127.0.0.1:8082`, protegido por token), grava uma configuração para o agente apontar para ele e instala o agente pelo Open VSX, se ainda não estiver.

## Créditos e licenças
VS Code (MIT, Microsoft), VSCodium (MIT), Roo Code (Apache-2.0). Veja `NOTICE.md`.
