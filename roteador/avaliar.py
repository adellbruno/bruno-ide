#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avaliador de modelos: mede, com tarefas de programação curtas e correção automática por testes,
qual dos seus modelos presta de verdade para cada nível.

Uso:  python avaliar.py                 testa todos os modelos do config.yaml que têm chave
      python avaliar.py --modelo gemini-2.5-flash
      python avaliar.py --rapido        só 6 tarefas (gasta menos cota)

AVISO: o código escrito pelo modelo é EXECUTADO no seu computador, numa pasta temporária, com limite
de tempo. As tarefas são simples (funções puras), mas só rode se você confia nos provedores configurados.
Cada tarefa é um pedido por modelo: isso consome cota. O resultado fica em dados/avaliacao.json.
"""
import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TAREFAS = [
    {"id": "soma_pares", "funcao": "soma_pares",
     "prompt": "Escreva em Python a função soma_pares(numeros) que recebe uma lista de inteiros e devolve a soma dos números pares.",
     "testes": "assert soma_pares([1,2,3,4]) == 6\nassert soma_pares([]) == 0\nassert soma_pares([-2, 3, 8]) == 6",
     "_gabarito": "def soma_pares(numeros):\n    return sum(n for n in numeros if n % 2 == 0)"},
    {"id": "palindromo", "funcao": "eh_palindromo",
     "prompt": "Escreva em Python a função eh_palindromo(texto) que devolve True se o texto é palíndromo, ignorando maiúsculas, espaços e pontuação.",
     "testes": "assert eh_palindromo('A man, a plan, a canal: Panama')\nassert not eh_palindromo('python')\nassert eh_palindromo('')\nassert eh_palindromo('Socorram-me, subi no onibus em Marrocos') is True",
     "_gabarito": "def eh_palindromo(texto):\n    t = [c.lower() for c in texto if c.isalnum()]\n    return t == t[::-1]"},
    {"id": "fatorial", "funcao": "fatorial",
     "prompt": "Escreva em Python a função fatorial(n) para inteiros n >= 0. Para n negativo, levante ValueError.",
     "testes": "assert fatorial(0) == 1\nassert fatorial(5) == 120\nassert fatorial(10) == 3628800\ntry:\n    fatorial(-1)\n    raise SystemExit(1)\nexcept ValueError:\n    pass",
     "_gabarito": "def fatorial(n):\n    if n < 0:\n        raise ValueError('negativo')\n    r = 1\n    for i in range(2, n + 1):\n        r *= i\n    return r"},
    {"id": "fibonacci", "funcao": "fibonacci",
     "prompt": "Escreva em Python a função fibonacci(n) que devolve o n-ésimo número de Fibonacci, com fibonacci(0)=0 e fibonacci(1)=1. Deve ser rápida para n=90.",
     "testes": "assert [fibonacci(i) for i in range(8)] == [0,1,1,2,3,5,8,13]\nassert fibonacci(90) == 2880067194370816120",
     "_gabarito": "def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a"},
    {"id": "frequencia", "funcao": "frequencia_palavras",
     "prompt": "Escreva em Python a função frequencia_palavras(texto) que devolve um dict com a quantidade de cada palavra, em minúsculas, separando por qualquer caractere que não seja letra.",
     "testes": "assert frequencia_palavras('Oi, oi! Tudo bem? tudo.') == {'oi': 2, 'tudo': 2, 'bem': 1}\nassert frequencia_palavras('') == {}",
     "_gabarito": "import re\ndef frequencia_palavras(texto):\n    d = {}\n    for p in re.findall(r'[^\\W\\d_]+', texto.lower()):\n        d[p] = d.get(p, 0) + 1\n    return d"},
    {"id": "cpf", "funcao": "validar_cpf",
     "prompt": "Escreva em Python a função validar_cpf(cpf) que recebe uma string (com ou sem pontos e traço) e devolve True se o CPF brasileiro é válido pelos dígitos verificadores. CPFs com todos os dígitos iguais são inválidos.",
     "testes": "assert validar_cpf('529.982.247-25') is True\nassert validar_cpf('52998224725') is True\nassert validar_cpf('529.982.247-24') is False\nassert validar_cpf('111.111.111-11') is False\nassert validar_cpf('123') is False",
     "_gabarito": "def validar_cpf(cpf):\n    d = [int(c) for c in cpf if c.isdigit()]\n    if len(d) != 11 or len(set(d)) == 1:\n        return False\n    for n in (9, 10):\n        s = sum(d[i] * (n + 1 - i) for i in range(n))\n        if (s * 10) % 11 % 10 != d[n]:\n            return False\n    return True"},
    {"id": "cnj", "funcao": "formatar_cnj",
     "prompt": "Escreva em Python a função formatar_cnj(numero) que recebe os 20 dígitos de um número de processo (string, pode ter pontuação) e devolve no formato NNNNNNN-DD.AAAA.J.TR.OOOO. Se não houver exatamente 20 dígitos, levante ValueError.",
     "testes": "assert formatar_cnj('12345670020238190001') == '1234567-00.2023.8.19.0001'\nassert formatar_cnj('1234567-00.2023.8.19.0001') == '1234567-00.2023.8.19.0001'\ntry:\n    formatar_cnj('123')\n    raise SystemExit(1)\nexcept ValueError:\n    pass",
     "_gabarito": "def formatar_cnj(numero):\n    d = ''.join(c for c in numero if c.isdigit())\n    if len(d) != 20:\n        raise ValueError('esperado 20 dígitos')\n    return f'{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:]}'"},
    {"id": "juros", "funcao": "juros_compostos",
     "prompt": "Escreva em Python a função juros_compostos(capital, taxa_mensal_percentual, meses) que devolve o montante final arredondado para 2 casas decimais. Exemplo: juros_compostos(1000, 1, 12) == 1126.83.",
     "testes": "assert juros_compostos(1000, 1, 12) == 1126.83\nassert juros_compostos(500, 0, 10) == 500.0\nassert juros_compostos(2000, 2, 1) == 2040.0",
     "_gabarito": "def juros_compostos(capital, taxa_mensal_percentual, meses):\n    return round(capital * (1 + taxa_mensal_percentual / 100) ** meses, 2)"},
    {"id": "agrupar", "funcao": "agrupar_por",
     "prompt": "Escreva em Python a função agrupar_por(itens, chave) que recebe uma lista e uma função chave e devolve um dict que mapeia cada valor de chave à lista de itens correspondentes, mantendo a ordem original.",
     "testes": "assert agrupar_por(['a','bb','c','dd','eee'], len) == {1:['a','c'], 2:['bb','dd'], 3:['eee']}\nassert agrupar_por([], len) == {}",
     "_gabarito": "def agrupar_por(itens, chave):\n    d = {}\n    for i in itens:\n        d.setdefault(chave(i), []).append(i)\n    return d"},
    {"id": "intervalos", "funcao": "mesclar_intervalos",
     "prompt": "Escreva em Python a função mesclar_intervalos(intervalos) que recebe uma lista de tuplas (inicio, fim), possivelmente fora de ordem, e devolve a lista de intervalos mesclados (os que se sobrepõem ou se tocam viram um), ordenada.",
     "testes": "assert mesclar_intervalos([(1,3),(2,6),(8,10),(15,18)]) == [(1,6),(8,10),(15,18)]\nassert mesclar_intervalos([(5,6),(1,5)]) == [(1,6)]\nassert mesclar_intervalos([]) == []",
     "_gabarito": "def mesclar_intervalos(intervalos):\n    out = []\n    for a, b in sorted(intervalos):\n        if out and a <= out[-1][1]:\n            out[-1] = (out[-1][0], max(out[-1][1], b))\n        else:\n            out.append((a, b))\n    return out"},
    {"id": "duplicados", "funcao": "remover_duplicados",
     "prompt": "Escreva em Python a função remover_duplicados(itens) que remove repetidos de uma lista mantendo a ordem da primeira aparição. Os itens podem ser não hasheáveis (listas).",
     "testes": "assert remover_duplicados([3,1,3,2,1]) == [3,1,2]\nassert remover_duplicados([[1],[2],[1]]) == [[1],[2]]\nassert remover_duplicados([]) == []",
     "_gabarito": "def remover_duplicados(itens):\n    out = []\n    for i in itens:\n        if i not in out:\n            out.append(i)\n    return out"},
    {"id": "romano", "funcao": "romano_para_int",
     "prompt": "Escreva em Python a função romano_para_int(s) que converte um número romano (maiúsculas) em inteiro.",
     "testes": "assert romano_para_int('III') == 3\nassert romano_para_int('IV') == 4\nassert romano_para_int('MCMXCIV') == 1994\nassert romano_para_int('LVIII') == 58",
     "_gabarito": "def romano_para_int(s):\n    v = {'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}\n    t = 0\n    for i, c in enumerate(s):\n        if i + 1 < len(s) and v[c] < v[s[i+1]]:\n            t -= v[c]\n        else:\n            t += v[c]\n    return t"},
    {"id": "parenteses", "funcao": "balanceado",
     "prompt": "Escreva em Python a função balanceado(texto) que devolve True se os parênteses (), colchetes [] e chaves {} do texto estão balanceados e bem aninhados, ignorando os demais caracteres.",
     "testes": "assert balanceado('([]{})')\nassert not balanceado('([)]')\nassert not balanceado('(((')\nassert balanceado('a(b)c')\nassert not balanceado(']')",
     "_gabarito": "def balanceado(texto):\n    par = {')':'(',']':'[','}':'{'}\n    pilha = []\n    for c in texto:\n        if c in '([{':\n            pilha.append(c)\n        elif c in par:\n            if not pilha or pilha.pop() != par[c]:\n                return False\n    return not pilha"},
    {"id": "brl", "funcao": "parse_valor_brl",
     "prompt": "Escreva em Python a função parse_valor_brl(texto) que converte valores como 'R$ 1.234,56' ou '1.234,56' ou '0,99' em float (1234.56, 1234.56, 0.99).",
     "testes": "assert parse_valor_brl('R$ 1.234,56') == 1234.56\nassert parse_valor_brl('1.234,56') == 1234.56\nassert parse_valor_brl('0,99') == 0.99\nassert parse_valor_brl('R$ 10') == 10.0",
     "_gabarito": "def parse_valor_brl(texto):\n    t = texto.replace('R$', '').strip().replace('.', '').replace(',', '.')\n    return float(t)"},
    {"id": "tempo", "funcao": "formatar_tempo",
     "prompt": "Escreva em Python a função formatar_tempo(segundos) que converte um inteiro de segundos para o formato HH:MM:SS com dois dígitos (horas podem passar de 99).",
     "testes": "assert formatar_tempo(0) == '00:00:00'\nassert formatar_tempo(3725) == '01:02:05'\nassert formatar_tempo(360000) == '100:00:00'",
     "_gabarito": "def formatar_tempo(segundos):\n    h, r = divmod(segundos, 3600)\n    m, s = divmod(r, 60)\n    return f'{h:02d}:{m:02d}:{s:02d}'"},
]
RAPIDAS = {"soma_pares", "palindromo", "cpf", "cnj", "intervalos", "parenteses"}


def extrair_codigo(resposta):
    blocos = re.findall(r"```(?:python|py)?\s*\n(.*?)```", resposta, re.S)
    if blocos:
        return max(blocos, key=len).strip()
    return resposta.strip()


def corrigir(tarefa, codigo, limite=10):
    """Roda o código do modelo mais os testes numa pasta temporária. True se tudo passar."""
    with tempfile.TemporaryDirectory() as d:
        arq = Path(d) / "solucao.py"
        arq.write_text(codigo + "\n\n" + tarefa["testes"] + "\n", encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", str(arq)], cwd=d, capture_output=True, timeout=limite)
            return r.returncode == 0
        except subprocess.TimeoutExpired:
            return False


async def avaliar(args):
    from rot.config import Settings
    from rot.engine import Engine
    from rot.state import State
    from rot.translate import BadResponse, to_anthropic

    s = Settings()
    eng = Engine(s, State(s.data_dir))
    tarefas = [t for t in TAREFAS if not args.rapido or t["id"] in RAPIDAS]
    cands, vistos = [], set()
    for tier, lst in s.tiers.items():
        for c in lst:
            k = (c["provedor"], c["modelo"])
            p = s.prov(c["provedor"])
            if p and p.keys and k not in vistos and (not args.modelo or args.modelo in c["modelo"]):
                vistos.add(k)
                cands.append((tier, c))
    for c in s.local_cands:
        if not args.modelo or args.modelo in c["modelo"]:
            cands.append((c["nivel"], c))
    if not cands:
        print("Nenhum modelo com chave encontrado. Rode: python app.py --chaves")
        return
    print(f"Avaliando {len(cands)} modelo(s) em {len(tarefas)} tarefas. Isso consome cota.\n")
    resultado = {}
    for tier, c in cands:
        prov = s.prov(c["provedor"])
        if prov.local:
            await eng.local.ensure(c)
        ok = erros = 0
        falhou = []
        for t in tarefas:
            body = {"max_tokens": 1500, "messages": [{"role": "user", "content": t["prompt"] + (
                " Responda só com o código em um bloco ```python.")}]}
            try:
                r = await eng.send(prov, prov.keys[0], c, body, False, {})
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                out = to_anthropic(r.json(), c["modelo"], 100)
                await r.aclose()
                texto = "".join(b.get("text", "") for b in out["content"] if b["type"] == "text")
                if corrigir(t, extrair_codigo(texto)):
                    ok += 1
                else:
                    falhou.append(t["id"])
            except (BadResponse, RuntimeError, Exception) as e:  # noqa: BLE001
                erros += 1
                falhou.append(f"{t['id']}(erro: {str(e)[:40]})")
            await asyncio.sleep(0.3)
        mk = f"{c['provedor']}/{c['modelo']}"
        resultado[mk] = {"nivel": tier, "acertos": ok, "total": len(tarefas), "erros_de_chamada": erros,
                         "falhou": falhou, "quando": time.strftime("%Y-%m-%d %H:%M")}
        print(f"  {mk:<46} {ok}/{len(tarefas)}  ({int(100 * ok / len(tarefas))}%)"
              + (f"   falhou: {', '.join(falhou[:6])}" if falhou else ""))
    (s.data_dir / "avaliacao.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nResultado salvo em dados/avaliacao.json. Use para ordenar os modelos de cada nível no config.yaml.")
    eng.local.stop()
    await eng.client.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default=None)
    ap.add_argument("--rapido", action="store_true")
    asyncio.run(avaliar(ap.parse_args()))
