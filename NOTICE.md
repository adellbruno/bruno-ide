# Avisos de terceiros
- **VS Code** (https://github.com/microsoft/vscode) - MIT, Microsoft Corporation.
- **VSCodium** (https://github.com/VSCodium/vscodium) - MIT. Scripts de build e patches deste repositório vêm dele.
- **Roo Code** (https://github.com/RooCodeInc/Roo-Code) - Apache-2.0. Instalado a partir do Open VSX na primeira execução; não é redistribuído neste repositório.
- **llama.cpp** (https://github.com/ggml-org/llama.cpp) - MIT. O `llama-server` é baixado no build (`bruno/fetch_binarios.sh`) e embutido no instalador; as variantes CUDA incluem as bibliotecas de execução da NVIDIA (cudart), distribuídas pela NVIDIA sob a licença do CUDA Toolkit.
- **Git for Windows / MinGit** (https://github.com/git-for-windows/git) - GPL-2.0. Baixado no build e embutido no instalador do Windows sem alterações; o código-fonte está no repositório acima.
- Os nomes "Visual Studio Code" e o logotipo são marcas da Microsoft. "Bruno IDE" é um nome provisório; os ícones atuais são os do VSCodium, troque por arte própria em `icons/`.
