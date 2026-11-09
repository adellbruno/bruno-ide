# Guia passo a passo (Bruno IDE)

Você não precisa saber programar para seguir isto. O computador do GitHub (grátis) faz a parte pesada de compilar o editor, porque ele precisa de bastante memória.

## Parte 1 - Gerar o instalador (uma vez, leva de 1 a 3 horas de espera)

1. Crie uma conta em **github.com** (se ainda não tem).
2. Clique em **+** (canto superior direito) > **New repository**. Nome: `bruno-ide`. Escolha **Public** (compilar em repositório público é gratuito e sem limite; o repositório não contém nenhuma chave sua). Crie.
3. Envie os arquivos deste projeto para lá. O jeito mais simples:
   - Instale o **GitHub Desktop** (desktop.github.com) e entre com sua conta.
   - Descompacte o zip. No GitHub Desktop: **File > Add local repository**, escolha a pasta `bruno-ide`, aceite criar o repositório; depois **Publish repository** (desmarque "Keep this code private").
4. No site do seu repositório, abra a aba **Actions**. Se aparecer um botão verde para habilitar, clique.
5. Na lista da esquerda, clique em **Compilar - Windows** > **Run workflow** > **Run workflow**.
6. Espere. Quando aparecer um ✔ verde, abra a execução e, no fim da página, em **Artifacts**, baixe **bin-x64**. Dentro está o instalador (`BrunoIDESetup-x64-...exe`).
7. Instale. O Windows pode mostrar "o Windows protegeu seu computador" (o instalador não é assinado digitalmente): clique em **Mais informações > Executar assim mesmo**.

**Se aparecer um ❌ vermelho:** abra a execução, clique no passo que falhou, copie as últimas ~100 linhas do log e me mande. É normal o primeiro build precisar de um ajuste.

## Parte 2 - Primeiro uso

1. Abra o **Bruno IDE**. Na barra inferior aparece **IA: ativo** (o roteador ligou sozinho).
2. Uma janela pergunta se quer cadastrar chaves: clique em **Cadastrar chave**, escolha o provedor e cole a chave. Repita para cada uma (várias chaves do mesmo provedor funcionam, ele alterna). Depois, a qualquer momento: `Ctrl+Shift+P` > "Bruno IDE: Cadastrar chave de API".
3. Na primeira abertura ele instala o agente sozinho (precisa de internet). Quando pedir, clique em **Recarregar agora**.
4. Abra uma pasta vazia (File > Open Folder), clique no ícone do agente (foguete/canguru) na barra lateral e escreva o que quer. Exemplo: *"Crie um programa em Python com janela que leia uma planilha de processos e gere um relatório em PDF. Teste e gere o executável para Windows."*
5. Clique na barra **IA: ativo** para abrir o painel (consumo, chaves, gastos).

## Modelos locais (GGUF)
`Ctrl+Shift+P` > "Bruno IDE: Baixar suporte a modelos locais" (baixa o llama-server) > "Bruno IDE: Abrir pasta de modelos locais (GGUF)" e coloque o arquivo `.gguf` lá > "Bruno IDE: Editar configuração" e mude `local: ativo: true`. Reinicie o roteador pelo comando "Bruno IDE: Reiniciar roteador".

## Autonomia (importante)
Por padrão o agente **executa comandos sozinho** (lista permitida: npm, python, pip, git, dotnet, cargo, etc.; comandos destrutivos ficam bloqueados). Para ele perguntar antes, mude em Configurações > "Bruno IDE: Autonomia" para `media` ou `perguntar`. Use sempre uma pasta de projeto separada e, de preferência, com git.

## Limites honestos
- O editor **ainda não foi compilado nem aberto por mim** (o ambiente que uso não tem memória para isso). Testei: a extensão com um VS Code simulado, o roteador como executável, e a configuração do agente contra o validador do próprio Roo Code. O primeiro build real é a prova final.
- O agente é o Roo Code (outro projeto). Com modelos gratuitos ou pequenos ele pode errar mais em tarefas grandes;.
- Sem atualização automática: para atualizar, rode o workflow de novo.
- Ícones ainda são os do VSCodium.
