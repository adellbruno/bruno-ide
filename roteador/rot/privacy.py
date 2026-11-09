# -*- coding: utf-8 -*-
"""Detector de dados sensíveis (processo CNJ, CPF, CNPJ, conta bancária, telefone, e-mail).

Só enxerga o que tem formato conhecido. Não entende contexto: o nome de uma parte, sozinho,
passa. Por isso existem também as pastas protegidas e a marca #local.
"""
import json
import re

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


# ------------------------------------------------------------------ validações
def _digits(s):
    return [int(c) for c in s if c.isdigit()]


def cpf_valido(s):
    d = _digits(s)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        soma = sum(d[i] * (n + 1 - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != d[n]:
            return False
    return True


def cnpj_valido(s):
    d = _digits(s)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for n, pesos in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        soma = sum(d[i] * pesos[i] for i in range(n))
        r = soma % 11
        dv = 0 if r < 2 else 11 - r
        if dv != d[n]:
            return False
    return True


def cnj_valido(s):
    """NNNNNNN-DD.AAAA.J.TR.OOOO: dígitos verificadores pelo módulo 97 (Resolução CNJ 65)."""
    d = "".join(str(x) for x in _digits(s))
    if len(d) != 20:
        return False
    nnnnnnn, dd, resto = d[:7], d[7:9], d[9:]
    return (98 - int(nnnnnnn + resto + "00") % 97) == int(dd)


# -------------------------------------------------------------------- padrões
RE_CNJ_F = re.compile(r"(?<!\d)\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}(?!\d)")
RE_CNJ_U = re.compile(r"(?<!\d)\d{20}(?!\d)")
RE_CPF_F = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
RE_CPF_U = re.compile(r"(?<!\d)\d{11}(?!\d)")
RE_CNPJ_F = re.compile(r"(?<![0-9A-Z])[0-9A-Z]{2}\.[0-9A-Z]{3}\.[0-9A-Z]{3}/[0-9A-Z]{4}-\d{2}(?!\d)")
RE_CNPJ_U = re.compile(r"(?<!\d)\d{14}(?!\d)")
RE_TEL = re.compile(r"(?<!\d)(?:\+55\s?)?\(?\d{2}\)?\s?9?\d{4}-\d{4}(?!\d)")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RE_CONTA = [
    re.compile(r"(?i)ag[eê]ncia[:\s]*\d{3,5}[^\n]{0,40}?conta[^\n\d]{0,20}\d{4,12}-?[\dxX]"),
    re.compile(r"(?i)\bc/?c[:\s]+\d{4,12}-?[\dxX]\b"),
    re.compile(r"(?i)\bconta\s+(?:corrente|poupan[çc]a)[:\s]*\d{4,12}-?[\dxX]\b"),
]

TODAS = ["processo", "cpf", "cnpj", "conta", "telefone", "email"]


def scan_text(text, categorias):
    """Devolve o conjunto de categorias encontradas (nomes, nunca os valores)."""
    achou = set()
    if not text or len(text) < 7:
        return achou
    cat = set(categorias)
    if "processo" in cat:
        if RE_CNJ_F.search(text) or any(cnj_valido(m) for m in RE_CNJ_U.findall(text)):
            achou.add("processo")
    if "cpf" in cat:
        if RE_CPF_F.search(text) or any(cpf_valido(m) for m in RE_CPF_U.findall(text)):
            achou.add("cpf")
    if "cnpj" in cat:
        if RE_CNPJ_F.search(text) or any(cnpj_valido(m) for m in RE_CNPJ_U.findall(text)):
            achou.add("cnpj")
    if "conta" in cat and any(r.search(text) for r in RE_CONTA):
        achou.add("conta")
    if "telefone" in cat and RE_TEL.search(text):
        achou.add("telefone")
    if "email" in cat and RE_EMAIL.search(text):
        achou.add("email")
    return achou


def _blocks_text(content):
    """Todo texto que o pedido leva: mensagens, resultados de ferramenta e argumentos."""
    if isinstance(content, str):
        yield content
        return
    for b in content or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            yield b.get("text", "")
        elif t == "tool_result":
            c = b.get("content")
            if isinstance(c, str):
                yield c
            else:
                for x in c or []:
                    if isinstance(x, dict) and x.get("type") == "text":
                        yield x.get("text", "")
        elif t == "tool_use":
            yield json.dumps(b.get("input", {}), ensure_ascii=False)


def scan_body(body, categorias, pastas_protegidas=()):
    """Procura dados sensíveis nas mensagens (não olha o prompt de sistema do Claude Code,
    que traz dados do ambiente como o e-mail da conta)."""
    achou = set()
    pastas = [p.lower().replace("\\", "/") for p in pastas_protegidas if p]
    for m in body.get("messages", []):
        for texto in _blocks_text(m.get("content")):
            texto = REMINDER.sub("", texto)
            achou |= scan_text(texto, categorias)
            if pastas:
                baixo = texto.lower().replace("\\\\", "/").replace("\\", "/")
                if any(p in baixo for p in pastas):
                    achou.add("pasta_protegida")
    return achou
