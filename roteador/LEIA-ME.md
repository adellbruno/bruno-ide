# Roteador de IA para o Claude Code (v2)

Um programa que fica entre o Claude Code e as IAs (Gemini, Groq, OpenRouter, DeepSeek, NVIDIA, modelos locais GGUF e, se quiser, o Claude oficial). Você só coloca as chaves; ele escolhe quem responde, alterna chaves, respeita limites e troca de IA quando algo falha.

## Como começar (3 passos)
1. Instale o **Python 3.10+** (python.org; no Windows marque "Add to PATH") e o **Claude Code**.
2. Rode `python instalar.py` (ou apenas `INICIAR.bat` no Windows / `iniciar.sh` no Mac e Linux — ele faz o mesmo preparo na primeira vez). O assistente pede suas chaves; as que você não tiver, deixe em branco.
3. Abra o terminal **na pasta do seu projeto** e rode o `INICIAR.bat` (ou `iniciar.sh`). Ele liga o roteador e abre o Claude Code já apontado para ele.

O painel (consumo, chaves, histórico) abre no endereço mostrado na janela do roteador, ou rode `python app.py --token`.

Quer que o roteador ligue sozinho com o Windows? Rode `INICIO_AUTOMATICO.bat` uma vez.

## O que ele faz
- **Escolhe o nível da tarefa** (simples / média / complexa) e usa a IA adequada. Se o Claude Code começar a errar seguidas vezes, sobe de nível sozinho; quando volta a dar certo, desce.
- **Marcas na sua mensagem:** `#rapido` `#medio` `#pro` (força o nível), `#local` (só modelo local), `#liberar` (libera dados sensíveis para a nuvem), `#economico` `#equilibrado` `#qualidade`, `#nocache`.
- **Chaves e limites:** alterna entre várias chaves do mesmo provedor, espera/pula quando bate limite, marca chave inválida e para de usá-la por 1 hora. Chaves nunca são gravadas em arquivos de estado.
- **Gastos:** provedores pagos (DeepSeek, Anthropic) têm limite diário/mensal em dólar; ao atingir, o roteador para de usá-los até você clicar em "liberar" no painel.
- **Privacidade:** detecta CNJ, CPF, CNPJ, conta bancária e telefone (e-mail opcional) na conversa e nas pastas protegidas do `config.yaml`. Se encontrar, a conversa inteira fica no modelo local. Se não houver modelo local configurado, o pedido é **recusado** (nada vai para a nuvem). `#liberar` desfaz isso conscientemente.
- **Modelos locais GGUF:** coloque o `.gguf` na pasta `modelos` e ligue `local: ativo: true`. `python app.py --hardware` diz o que cabe no seu computador; `python app.py --instalar-llama` baixa o llama-server. O roteador carrega e descarrega o modelo sozinho. Em `local.uso` escolha: `reserva` (padrão, só quando a nuvem falha), `principal` ou `so_privado`.
- **Economia de tokens** (desligada por padrão; `economia.modo: leve` ou `forte`): encurta resultados antigos de ferramentas, enxuga a lista de ferramentas, compacta conversa longa e guarda respostas repetidas. Nas medições, o Claude Code manda ~15 mil tokens por pedido, ~10 mil só de definição de ferramentas.
- **Painel:** consumo por provedor, chaves, gastos, histórico (exportável em CSV), alertas e sugestão de nova ordem de modelos aprendida com o uso (você aprova).

## Comandos úteis
| Comando | Para quê |
|---|---|
| `python app.py` | liga o roteador |
| `python app.py --testar` | testa cada modelo (texto, ferramenta, streaming) — gasta um pouco de cota |
| `python app.py --modelos` | confere se os modelos do config ainda existem |
| `python app.py --chaves` | cadastra chaves (arquivo `.env` ou cofre do sistema, se `keyring` instalado) |
| `python app.py --hardware` | mede o computador |
| `python testes.py` | 37 testes automáticos (sem internet nem chaves) |
| `python avaliar.py` | 14 tarefas de programação corrigidas rodando o código; mostra no painel qual modelo rende melhor |

## Limites honestos
- Foi testado com provedores **simulados** e com o Claude Code real contra eles. **Não foi testado com chaves reais**: o primeiro dia de uso é a verdadeira validação (veja `CHECKLIST_DIA.md`).
- Modelos gratuitos e locais pequenos são **menos capazes** que o Claude; para tarefas difíceis podem errar mais ou travar em laços de ferramenta.
- Os modelos locais foram testados só com um llama-server simulado; o download automático do llama-server (`--instalar-llama`) e o cofre `keyring` não foram exercitados.
- O detector de privacidade acha padrões (números), **não** nomes de pessoas nem endereços. Revise pastas sensíveis em `privacidade.pastas_protegidas`.
- Cotas e nomes de modelos gratuitos mudam; se um modelo sumir, o aviso aparece no painel e o roteador usa o próximo.
- Não há ícone de bandeja; o controle é pelo painel.
- Termos de uso: confira se o uso de cada provedor gratuito permite o seu caso.
