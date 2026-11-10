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

1. Abra o **Bruno IDE**. Na barra lateral esquerda aparece o ícone **Bruno IDE** (um chip): é a **tela inicial**, em português, com botões para tudo. Na barra de baixo, **IA: ativo** mostra que o roteador ligou sozinho.
2. O editor instala sozinho o idioma **português do Brasil** e o agente (precisa de internet). Quando aparecer "Recarregar agora", clique. Na primeira vez pode pedir duas vezes.
3. Clique em **Cadastrar chave de API** e cole suas chaves, uma por vez. Pode cadastrar várias do mesmo provedor: ele alterna sozinho.
4. Clique em **Conversar com o agente** (ou no ícone do canguru). Abra antes uma pasta de projeto: **Arquivo > Abrir Pasta**.
5. Escreva em português o que quer. Escolha o modo no painel do agente:
   - **Perguntar**: só conversa e explica, não mexe em nada (é o "chat").
   - **Código**: cria e edita arquivos e roda comandos.
   - **Arquiteto**: planeja antes de programar.
   - **Depurar**: acha e corrige erros.
6. Exemplo: *"Crie um programa em Python com janela que leia uma planilha de processos e gere um relatório em PDF. Teste e gere o executável para Windows."*
7. **Painel de consumo** (botão na tela inicial ou clique em "IA: ativo") mostra o uso de cada chave.

## Modelo local (GGUF): botão "Instalar modelo local"
Na tela inicial, clique em **Instalar modelo local (GGUF)**. O assistente:
1. Mede o seu computador e diz que tamanho de modelo cabe.
2. Oferece **baixar um modelo recomendado** (Qwen2.5-Coder, de 1,5 a 14 bilhões de parâmetros, só os que cabem na sua máquina) **ou usar um arquivo .gguf que você já tem**.
3. Baixa o "motor" (llama-server) se faltar.
4. Pergunta quando usar: só como reserva (padrão), sempre antes da nuvem, ou só para dados sensíveis.
5. Liga o modo local e reinicia o roteador. Depois, pela mesma opção, dá para desligar, trocar ou abrir a pasta de modelos.

## Autonomia (importante)
Por padrão o agente **executa comandos sozinho** (lista permitida: npm, python, pip, git, dotnet, cargo, etc.; comandos destrutivos ficam bloqueados). Para ele perguntar antes, mude em Configurações > "Bruno IDE: Autonomia" para `media` ou `perguntar`. Use sempre uma pasta de projeto separada e, de preferência, com git.

## Limites honestos
- O editor já foi compilado no GitHub e abre. As novidades desta atualização (tela inicial, assistente GGUF, idioma) foram testadas com um VS Code simulado e só terão a prova final no próximo build.
- O agente é o Roo Code (outro projeto). Com modelos gratuitos ou pequenos ele pode errar mais em tarefas grandes.
- Sem atualização automática: para atualizar, rode o workflow de novo.
- Ícones ainda são os do VSCodium.
- O download automático do motor (llama-server) e dos modelos (Hugging Face) foi escrito mas não pude testar contra a internet real daqui; se falhar, o assistente mostra o caminho manual.
