# -*- coding: utf-8 -*-
"""Decisão do nível da tarefa (simples, médio, complexo), com subida e descida automáticas."""
import hashlib
import re

from .translate import REMINDER, text_of

NIVEIS = ["simples", "medio", "complexo"]
TAGS_FIXAS = {"#local", "#liberar", "#economico", "#equilibrado", "#qualidade", "#nocache"}


class Sessao:
    """Memória de uma conversa (pelo texto do primeiro pedido)."""

    def __init__(self):
        self.base = "simples"
        self.tier = "simples"
        self.sensivel = False
        self.liberado = False
        self.force_local = False
        self.last_model = None
        self.escalou = False


def last_user_info(body):
    """Texto do último pedido do usuário (sem lembretes do sistema) e se é continuação de agente."""
    for m in reversed(body.get("messages", [])):
        if m.get("role") != "user":
            continue
        c = m["content"]
        if isinstance(c, str):
            return REMINDER.sub("", c).strip(), False
        texts = [REMINDER.sub("", b.get("text", "")).strip() for b in c if b.get("type") == "text"]
        texts = [t for t in texts if t]
        has_tr = any(b.get("type") == "tool_result" for b in c)
        return "\n".join(texts), (has_tr and not texts)
    return "", False


def fingerprint(body):
    for m in body.get("messages", []):
        if m.get("role") == "user":
            t = REMINDER.sub("", text_of(m["content"])).strip()
            if t:
                return hashlib.sha1(t[:500].encode("utf-8", "replace")).hexdigest()[:16]
    return "?"


def parse_tags(text, cfg):
    conhecidas = set(TAGS_FIXAS) | set((cfg["roteamento"].get("atalhos") or {}).keys())
    achadas = set(re.findall(r"#[\wçãõéêíóúâ]+", text.lower()))
    return achadas & conhecidas


def _resultados_do_fim(body):
    """Percorre de trás para frente as mensagens de resultado de ferramenta do agente."""
    for m in reversed(body.get("messages", [])):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if not isinstance(c, list):
            return
        res = [b for b in c if isinstance(b, dict) and b.get("type") == "tool_result"]
        if not res:
            return  # mensagem de texto do usuário: fim da sequência
        yield res


def _eh_erro(b):
    if b.get("is_error"):
        return True
    c = b.get("content")
    t = c if isinstance(c, str) else text_of(c or [])
    return bool(re.match(r"\s*Exit code [1-9]", t or ""))


def failure_streak(body):
    n = 0
    for res in _resultados_do_fim(body):
        if any(_eh_erro(b) for b in res):
            n += 1
        else:
            break
    return n


def ok_streak(body):
    n = 0
    for res in _resultados_do_fim(body):
        if any(_eh_erro(b) for b in res):
            break
        n += 1
    return n


def _sobe(tier):
    return NIVEIS[min(NIVEIS.index(tier) + 1, 2)]


def pontuar(body, text, rot):
    low = text.lower()
    model = (body.get("model") or "").lower()
    n = len(text)
    score = (n > 200) + (n > 1500) + (n > 5000)
    score += min(sum(1 for w in rot.get("palavras_complexas", []) if w in low), 4)
    score -= min(sum(1 for w in rot.get("palavras_simples", []) if w in low), 2)
    if (body.get("thinking") or {}).get("type") == "enabled":
        score += 3
    if "opus" in model:
        score += 2
    if len(body.get("messages", [])) > 60:
        score += 1
    return score


def tier_por_pontos(score, rot):
    if score >= rot.get("complexo_a_partir_de", 3):
        return "complexo"
    if score >= rot.get("medio_a_partir_de", 1):
        return "medio"
    return "simples"


def decidir(body, cfg, sess, text, cont, tags):
    """Devolve (nivel, motivo, duvidoso). `duvidoso` pede o classificador opcional."""
    rot = cfg["roteamento"]
    model = (body.get("model") or "").lower()
    esc = rot.get("escalada", {})

    for tag, tier in (rot.get("atalhos") or {}).items():
        if tag in tags:
            sess.base = sess.tier = tier
            return tier, f"atalho {tag}", False

    if "haiku" in model:
        return "simples", "tarefa leve interna", False

    if cont and sess.tier:  # o agente continua o mesmo trabalho
        atual = sess.tier
        if esc.get("ativo", True):
            f = failure_streak(body)
            if f >= esc.get("falhas_seguidas", 2) and atual != "complexo":
                sess.tier = _sobe(atual)
                sess.escalou = True
                return sess.tier, f"subiu de nível ({f} erros seguidos)", False
            if sess.tier != sess.base and ok_streak(body) >= esc.get("descer_apos_sucessos", 6):
                sess.tier = sess.base
                sess.escalou = False
                return sess.tier, "voltou ao nível original", False
        return sess.tier, "continuação", False

    score = pontuar(body, text, rot)
    tier = tier_por_pontos(score, rot)
    duvidoso = rot.get("medio_a_partir_de", 1) <= score < rot.get("complexo_a_partir_de", 3) and len(text) > 40
    if "#qualidade" in tags:
        tier = _sobe(tier)
    sess.base = sess.tier = tier
    sess.escalou = False
    return tier, f"pontuação {score}", duvidoso
