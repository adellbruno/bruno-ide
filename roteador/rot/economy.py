# -*- coding: utf-8 -*-
"""Economia de tokens: medir, cortar resultados enormes, enxugar ferramentas, compactar conversa, cache."""
import copy
import hashlib
import json
import re
import time

from .translate import REMINDER, text_of


def _chars(x):
    return len(json.dumps(x, ensure_ascii=False)) if x else 0


def breakdown(body):
    """Estimativa (em tokens) de quanto cada parte do pedido pesa."""
    msgs = body.get("messages", [])
    ultima = 0
    if msgs:
        ultima = _chars(msgs[-1].get("content"))
    hist = sum(_chars(m.get("content")) for m in msgs[:-1])
    return {
        "sistema": _chars(body.get("system")) // 3,
        "ferramentas": _chars(body.get("tools")) // 3,
        "historico": hist // 3,
        "ultima": ultima // 3,
    }


# ------------------------------------------------------- cortar resultados enormes
def _cut(text, limite):
    if len(text) <= limite:
        return text
    ini = int(limite * 0.65)
    fim = int(limite * 0.25)
    omit = len(text) - ini - fim
    return f"{text[:ini]}\n\n[... {omit} caracteres omitidos pelo roteador para economizar tokens ...]\n\n{text[-fim:]}"


def truncar_resultados(body, antigos, recente):
    """Corta resultados de ferramenta muito longos. Os da última rodada têm limite maior."""
    msgs = body.get("messages", [])
    ultimo_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    novo, mudou = [], False
    for i, m in enumerate(msgs):
        c = m.get("content")
        if m.get("role") != "user" or not isinstance(c, list):
            novo.append(m)
            continue
        lim = recente if i == ultimo_user else antigos
        blocos = []
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                cont = b.get("content")
                if isinstance(cont, str) and len(cont) > lim:
                    b = {**b, "content": _cut(cont, lim)}
                    mudou = True
                elif isinstance(cont, list):
                    partes = []
                    for x in cont:
                        if isinstance(x, dict) and x.get("type") == "text" and len(x.get("text", "")) > lim:
                            x = {**x, "text": _cut(x["text"], lim)}
                            mudou = True
                        partes.append(x)
                    b = {**b, "content": partes}
            blocos.append(b)
        novo.append({**m, "content": blocos})
    if not mudou:
        return body
    return {**body, "messages": novo}


# ------------------------------------------------------------ ferramentas enxutas
def enxugar_ferramentas(body, maximo):
    """Mantém as ferramentas já usadas, as que casam com o pedido e as primeiras da lista."""
    tools = body.get("tools") or []
    if len(tools) <= maximo:
        return body
    usadas = set()
    for m in body.get("messages", []):
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    usadas.add(b.get("name"))
    texto = ""
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            texto = REMINDER.sub("", text_of(m["content"]) if not isinstance(m["content"], list)
                                 else text_of(m["content"])).lower()
            if texto.strip():
                break
    palavras = set(re.findall(r"[a-zà-ú]{4,}", texto))

    def pontos(t):
        nome = t.get("name", "")
        if nome in usadas:
            return 100
        desc = (nome + " " + t.get("description", "")[:200]).lower()
        return sum(1 for w in palavras if w in desc)

    ordem = sorted(range(len(tools)), key=lambda i: (-pontos(tools[i]), i))
    fica = sorted(ordem[:maximo])
    return {**body, "tools": [tools[i] for i in fica]}


# ----------------------------------------------------------- compactar conversa
def _tem_resultado(m):
    return isinstance(m.get("content"), list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])


def _resumo(msgs):
    pedidos, arquivos, erros, comandos = [], [], 0, []
    for m in msgs:
        c = m.get("content")
        if m.get("role") == "user" and not _tem_resultado(m):
            t = REMINDER.sub("", text_of(c)).strip()
            if t:
                pedidos.append(t[:300])
        if isinstance(c, list):
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    for k in ("file_path", "path", "notebook_path"):
                        if inp.get(k) and inp[k] not in arquivos:
                            arquivos.append(inp[k])
                    if inp.get("command"):
                        comandos.append(str(inp["command"])[:120])
                elif b.get("type") == "tool_result" and b.get("is_error"):
                    erros += 1
    linhas = ["[Resumo automático do início desta conversa, feito pelo roteador para economizar tokens]"]
    if pedidos:
        linhas.append("Pedidos do usuário até aqui:")
        linhas += [f"- {p}" for p in pedidos[-8:]]
    if arquivos:
        linhas.append("Arquivos já lidos ou alterados: " + ", ".join(arquivos[-25:]))
    if comandos:
        linhas.append("Últimos comandos executados: " + " | ".join(comandos[-6:]))
    if erros:
        linhas.append(f"Ferramentas com erro nesse trecho: {erros}")
    linhas.append("(Releia os arquivos se precisar dos detalhes.)")
    return "\n".join(linhas)


def compactar(body, manter_ultimas):
    """Troca as mensagens antigas por um resumo mecânico, sem quebrar pares ferramenta/resultado."""
    msgs = body.get("messages", [])
    if len(msgs) <= manter_ultimas + 2:
        return body
    corte = None
    for i in range(len(msgs) - manter_ultimas, 0, -1):  # primeiro ponto seguro: mensagem de texto do usuário
        m = msgs[i]
        if m.get("role") == "user" and not _tem_resultado(m):
            corte = i
            break
    if corte is None or corte < 2:
        return body
    resumo = _resumo(msgs[:corte])
    alvo = copy.deepcopy(msgs[corte])
    if isinstance(alvo["content"], str):
        alvo["content"] = resumo + "\n\n" + alvo["content"]
    else:
        alvo["content"] = [{"type": "text", "text": resumo}] + list(alvo["content"])
    return {**body, "messages": [alvo] + msgs[corte + 1:]}


# --------------------------------------------------------------------- cache
class RespostaCache:
    def __init__(self):
        self.d = {}

    @staticmethod
    def chave(body, extra=""):
        base = json.dumps([body.get("system"), body.get("messages"), body.get("tools"),
                           body.get("max_tokens"), body.get("stream"), extra], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    def get(self, k):
        v = self.d.get(k)
        if not v:
            return None
        if v[0] < time.time():
            self.d.pop(k, None)
            return None
        return v[1]

    def put(self, k, valor, segundos):
        if len(self.d) > 200:
            for kk in sorted(self.d, key=lambda x: self.d[x][0])[:50]:
                self.d.pop(kk, None)
        self.d[k] = (time.time() + segundos, valor)
