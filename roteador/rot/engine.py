# -*- coding: utf-8 -*-
"""Motor do roteador: escolhe o modelo, alterna chaves, troca de provedor e registra tudo."""
import asyncio
import difflib
import json
import re
import time
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path

import httpx
from fastapi.responses import JSONResponse, StreamingResponse

from . import economy, tiering
from .config import log
from .local import LocalManager, hardware, recomendar
from .privacy import scan_body
from .state import month, sk, today
from .translate import (BadResponse, prime_stream, sse, to_anthropic, to_openai,
                        translate_stream, _chain)

CHAIN = {
    "simples": ("simples", "medio", "complexo"),
    "medio": ("medio", "complexo", "simples"),
    "complexo": ("complexo", "medio", "simples"),
}


def err(status, msg, typ="api_error", headers=None):
    return JSONResponse({"type": "error", "error": {"type": typ, "message": msg}},
                        status_code=status, headers=headers)


def estimate_tokens(body):
    raw = json.dumps([body.get("system"), body.get("messages"), body.get("tools")], ensure_ascii=False)
    return len(raw) // 3


class Engine:
    def __init__(self, settings, state):
        self.s = settings
        self.st = state
        self.sessions = OrderedDict()
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
        self.local = LocalManager(settings, self.client)
        self.cache = economy.RespostaCache()
        self.avail = {}
        self.avail_at = 0.0
        self._checking = False
        self.blocked_today = {}  # data -> pedidos barrados por privacidade
        self.inicio = time.time()

    # ------------------------------------------------------------------ sessões
    def sessao(self, fp):
        s = self.sessions.get(fp)
        if s is None:
            s = tiering.Sessao()
            self.sessions[fp] = s
            while len(self.sessions) > 500:
                self.sessions.popitem(last=False)
        else:
            self.sessions.move_to_end(fp)
        return s

    # -------------------------------------------------------------------- chaves
    def pick_key(self, prov, model):
        now, n = time.time(), len(prov.keys)
        wait = None
        for i in range(n):
            ki = (prov.rr + i) % n
            kid = prov.kids[ki]
            if self.st.dead.get(sk(prov.name, kid), 0) > now:
                continue
            if self.st.cooldown.get(sk(prov.name, kid, model), 0) > now:
                continue
            if prov.daily_limit and self.st.daily.get(sk(prov.name, kid, today()), 0) >= prov.daily_limit:
                continue
            if prov.rpm:
                dq = self.st.rpm.setdefault((prov.name, kid), deque())
                while dq and dq[0] <= now - 60:
                    dq.popleft()
                if len(dq) >= prov.rpm:
                    w = dq[0] + 60 - now
                    wait = w if wait is None else min(wait, w)
                    continue
            prov.rr = (ki + 1) % n
            return ki, None
        return None, wait

    def note_request(self, prov, kid):
        self.st.rpm.setdefault((prov.name, kid), deque()).append(time.time())

    def penalize(self, prov, ki, model, base, why):
        k = sk(prov.name, prov.kids[ki], model)
        self.st.fails[k] = self.st.fails.get(k, 0) + 1
        wait = min(base * 2 ** (self.st.fails[k] - 1), 1800)
        self.st.cooldown[k] = time.time() + wait
        self.st.touch()
        log(f"[!] {prov.name}/{model} (chave {ki + 1}): {why} -> pausa de {int(wait)}s")

    def pressao(self, prov):
        """Fração da cota diária que ainda resta (1.0 = cheia ou sem limite conhecido)."""
        if not prov.daily_limit:
            return 1.0
        total = prov.daily_limit * max(1, len(prov.keys))
        usado = sum(self.st.daily.get(sk(prov.name, k, today()), 0) for k in prov.kids)
        return max(0.0, (total - usado) / total)

    def gasto_bloqueado(self, prov):
        if not (prov.usd_dia or prov.usd_mes):
            return None
        extra = self.st.gasto_extra.get(prov.name) or {}
        extra_usd = extra.get("usd", 0.0) if extra.get("data") == today() else 0.0
        if prov.usd_dia and self.st.spent(prov.name, "dia") >= prov.usd_dia + extra_usd:
            return "dia"
        if prov.usd_mes and self.st.spent(prov.name, "mes") >= prov.usd_mes + extra_usd:
            return "mês"
        return None

    # ------------------------------------------------------------- lista de modelos
    def modelo_ausente(self, cand):
        ids = self.avail.get(cand["provedor"])
        return bool(ids) and cand["modelo"] not in ids

    async def check_models(self):
        if self._checking:
            return
        self._checking = True
        try:
            for name, prov in list(self.s.providers.items()):
                if prov.local or not prov.keys:
                    continue
                try:
                    if prov.kind == "anthropic":
                        r = await self.client.get(prov.base_url + "/v1/models", params={"limit": 1000},
                                                  headers={"x-api-key": prov.keys[0], "anthropic-version": "2023-06-01"},
                                                  timeout=20)
                    else:
                        r = await self.client.get(prov.base_url + "/models", timeout=20,
                                                  headers={"Authorization": f"Bearer {prov.keys[0]}", **prov.headers})
                    if r.status_code != 200:
                        self.avail[name] = None
                        continue
                    ids = set()
                    for d in r.json().get("data", []):
                        i = d.get("id") or d.get("name") or ""
                        ids.add(i[7:] if i.startswith("models/") else i)
                    self.avail[name] = ids or None
                except (httpx.HTTPError, ValueError):
                    self.avail[name] = None
            self.avail_at = time.time()
            for a in self.ausentes():
                self.st.event("modelo_ausente", f"{a['provedor']}/{a['modelo']} não existe mais"
                              + (f"; parecido: {a['sugestoes'][0]}" if a["sugestoes"] else ""))
        finally:
            self._checking = False

    def ausentes(self):
        out, vistos = [], set()
        for tier, lst in self.s.tiers.items():
            for c in lst:
                k = (c["provedor"], c["modelo"])
                if k in vistos or not self.s.prov(c["provedor"]) or not self.s.prov(c["provedor"]).keys:
                    continue
                vistos.add(k)
                if self.modelo_ausente(c):
                    sug = difflib.get_close_matches(c["modelo"], sorted(self.avail[c["provedor"]]), n=3, cutoff=0.5)
                    out.append({"provedor": c["provedor"], "modelo": c["modelo"], "nivel": tier, "sugestoes": sug})
        return out

    # -------------------------------------------------------------------- cadeia
    def _aprovada(self, tier, lst):
        ordem = self.st.ordem.get(tier)
        if not ordem:
            return lst
        pos = {k: i for i, k in enumerate(ordem)}
        return sorted(lst, key=lambda c: pos.get(f"{c['provedor']}/{c['modelo']}", 10_000))

    def _distribuir(self, lst):
        """Passa para o fim da fila quem está com a cota quase no fim."""
        baixo = self.s.cfg["roteamento"].get("cota_baixa_abaixo_de", 0.25)
        bons, ruins = [], []
        for c in lst:
            p = self.s.prov(c["provedor"])
            (ruins if p and self.pressao(p) < baixo else bons).append(c)
        return bons + ruins

    def build_chain(self, tier, est, has_tools, local_only, profile):
        cfg = self.s.cfg
        ver = cfg.get("verificar_modelos", "avisar")

        def ok(c):
            p = self.s.prov(c["provedor"])
            if not p or not p.keys or p.name in self.st.paused:
                return False
            if c.get("local"):
                if not Path(c["arquivo"]).exists():
                    return False
            elif ver == "pular" and self.modelo_ausente(c):
                return False
            calib = self.st.calib.get(f"{c['provedor']}/{c['modelo']}", 1.0)
            if est * calib > c.get("contexto", 120000) * 0.9:
                return False
            if has_tools and c.get("tools") is False:
                return False
            if self.gasto_bloqueado(p):
                return False
            return True

        locais = [c for c in self.s.local_cands if ok(c)]
        if local_only:
            return sorted(locais, key=lambda c: 0 if c["nivel"] == tier else 1)
        out, seen = [], set()
        for t in CHAIN[tier]:
            lst = self._aprovada(t, list(self.s.tiers.get(t) or []))
            if cfg["roteamento"].get("distribuir_carga", True):
                lst = self._distribuir(lst)
            for c in lst:
                k = (c["provedor"], c["modelo"])
                if k not in seen and ok(c):
                    seen.add(k)
                    out.append(c)
        if profile == "economico":
            def pago(c):
                p = self.s.prov(c["provedor"])
                return 1 if (p and p.pago) else 0
            out.sort(key=pago)
        uso = cfg["local"].get("uso", "reserva")
        if uso == "reserva":
            out += locais
        elif uso == "principal":
            out = sorted(locais, key=lambda c: 0 if c["nivel"] == tier else 1) + out
        return out

    # ----------------------------------------------------------------- upstream
    async def send(self, prov, key, cand, body, stream, fwd):
        if prov.kind == "anthropic":
            payload = dict(body)
            payload["model"], payload["stream"] = cand["modelo"], stream
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
                       "content-type": "application/json", **fwd}
            url = prov.base_url + "/v1/messages"
        else:
            payload = to_openai(body, cand, stream, include_usage=prov.stream_usage)
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **prov.headers}
            url = prov.base_url + "/chat/completions"
        req = self.client.build_request("POST", url, json=payload, headers=headers)
        return await self.client.send(req, stream=stream)

    # ------------------------------------------------------------ classificador
    async def llm_classify(self, text):
        cfg = self.s.cfg["roteamento"]["classificador_llm"]
        for cand in self.s.tiers.get("simples", []):
            prov = self.s.prov(cand["provedor"])
            if not prov or not prov.keys or prov.name in self.st.paused or prov.local:
                continue
            ki, _ = self.pick_key(prov, cand["modelo"])
            if ki is None:
                continue
            body = {"max_tokens": 8, "messages": [{"role": "user", "content": (
                "Classifique a tarefa de programação abaixo como simples, medio ou complexo. "
                "Responda só uma dessas três palavras.\n\nTarefa: " + text[:1500])}]}
            try:
                self.note_request(prov, prov.kids[ki])
                r = await asyncio.wait_for(self.send(prov, prov.keys[ki], cand, body, False, {}),
                                           timeout=float(cfg.get("timeout_s", 8)))
                if r.status_code != 200:
                    continue
                out = (r.json()["choices"][0]["message"].get("content") or "").lower()
                for t in ("complexo", "medio", "médio", "simples"):
                    if t in out:
                        return "medio" if t == "médio" else t
            except Exception:
                continue
        return None

    # -------------------------------------------------------- pedido principal
    async def handle(self, body, headers):
        t0 = time.time()
        self.s.reload_if_changed()
        if self.s.cfg.get("verificar_modelos") != "desligado" and time.time() - self.avail_at > 6 * 3600 \
                and not self._checking:
            asyncio.ensure_future(self.check_models())
        cfg = self.s.cfg
        stream = bool(body.get("stream"))
        text, cont = tiering.last_user_info(body)
        tags = tiering.parse_tags(text, cfg)
        fp = tiering.fingerprint(body)
        sess = self.sessao(fp)
        prev_tier, prev_esc = sess.tier, sess.escalou

        # ---- privacidade
        priv = cfg["privacidade"]
        cats = set()
        if priv.get("ativo"):
            cats = scan_body(body, priv.get("detectar", []), priv.get("pastas_protegidas") or [])
        if "#local" in tags:
            sess.force_local = True
        if "#liberar" in tags:
            sess.liberado = True
        if cats:
            sess.sensivel = True
        local_only = sess.force_local or (sess.sensivel and not sess.liberado)

        # ---- nível
        tier, why, duvidoso = tiering.decidir(body, cfg, sess, text, cont, tags)
        if sess.escalou and not prev_esc and sess.last_model:
            self.st.learn_add(prev_tier, sess.last_model, "escaladas")
            self.st.event("escalada", f"tarefa subiu de {prev_tier} para {tier}")
        if duvidoso and cfg["roteamento"]["classificador_llm"].get("ativo") and not local_only:
            t = await self.llm_classify(text)
            if t:
                tier, why = t, f"classificador ({t})"
                sess.base = sess.tier = t
        profile = "economico" if "#economico" in tags else ("qualidade" if "#qualidade" in tags else "equilibrado")

        # ---- economia de tokens
        eco = cfg["economia"]
        b2 = body
        if eco["cortar_resultados"].get("ativo"):
            b2 = economy.truncar_resultados(b2, eco["cortar_resultados"]["antigos_chars"],
                                            eco["cortar_resultados"]["recente_chars"])
        est = estimate_tokens(b2)
        if eco["compactar_conversa"].get("ativo") and est > eco["compactar_conversa"]["a_partir_de_tokens"]:
            b2 = economy.compactar(b2, eco["compactar_conversa"]["manter_ultimas"])
        if eco["ferramentas_enxutas"].get("ativo"):
            b2 = economy.enxugar_ferramentas(b2, eco["ferramentas_enxutas"]["max"])
        est = estimate_tokens(b2)
        pesos = economy.breakdown(b2)

        ctx = {"tier": tier, "why": why, "t0": t0, "sess": sess, "cats": cats, "local_only": local_only,
               "pesos": pesos, "stream": stream, "trocas": 0,
               "externo_sensivel": bool(sess.sensivel and sess.liberado and not sess.force_local)}
        if ctx["externo_sensivel"] and cats:
            self.st.event("liberado", "pedido com dado sensível enviado à internet (#liberar)")

        # ---- cache
        ckey = None
        if eco["cache_respostas"].get("ativo") and "#nocache" not in tags:
            ckey = economy.RespostaCache.chave(b2, "L" if local_only else "E")
            hit = self.cache.get(ckey)
            if hit is not None:
                self.st.add_history({"nivel": tier, "motivo": "cache", "prov": "cache", "modelo": "-",
                                     "status": "ok", "ms": int((time.time() - t0) * 1000)})
                if stream:
                    async def replay():
                        for ch in hit:
                            yield ch
                    return StreamingResponse(replay(), media_type="text/event-stream")
                return JSONResponse(hit)

        # ---- cadeia de candidatos
        has_tools = bool(b2.get("tools"))
        chain = self.build_chain(tier, est, has_tools, local_only, profile)
        if local_only and not chain:
            if priv.get("sem_local") == "avisar":
                self.st.event("privacidade", "dado sensível saiu para a internet: não há modelo local (modo avisar)")
                local_only = False
                ctx["local_only"] = False
                ctx["externo_sensivel"] = True
                chain = self.build_chain(tier, est, has_tools, False, profile)
            else:
                d = today()
                self.blocked_today[d] = self.blocked_today.get(d, 0) + 1
                self.st.add_history({"nivel": tier, "motivo": why, "prov": "-", "modelo": "-",
                                     "status": "bloqueado_privacidade", "detectado": sorted(cats),
                                     "ms": int((time.time() - t0) * 1000)})
                return err(403, "Dados sensíveis detectados (" + ", ".join(sorted(cats) or ["#local"]) + ") e nenhum "
                                "modelo local está disponível. Nada foi enviado à internet. Configure um modelo "
                                "local, ou escreva #liberar na mensagem para permitir o envio externo.",
                           "permission_error")
        if not chain:
            return err(503, "Nenhuma chave de API configurada, provedores pausados ou contexto grande demais. "
                            "Confira o arquivo .env e o painel.")

        fwd = {h: headers[h] for h in ("anthropic-beta",) if h in headers}
        shown = body.get("model", "claude")
        log(f"pedido: nível {tier} ({why}), ~{est} tokens" + (", PRIVADO" if local_only else ""))

        for cand in chain:
            prov, model = self.s.prov(cand["provedor"]), cand["modelo"]
            tentativas = 0
            while True:
                ki, wait = self.pick_key(prov, model)
                if ki is None:
                    if wait is not None and wait <= 3 and tentativas < 2:
                        tentativas += 1
                        await asyncio.sleep(wait + 0.05)
                        continue
                    break
                acao, resp = await self.attempt(prov, ki, cand, b2, stream, fwd, est, ctx, shown, ckey)
                if acao == "ok":
                    return resp
                ctx["trocas"] += 1
                if acao == "cand":
                    break

        wait = self.min_wait(chain)
        self.st.add_history({"nivel": tier, "motivo": why, "prov": "-", "modelo": "-", "status": "sem_provedor",
                             "ms": int((time.time() - t0) * 1000)})
        log(f"[!] todos os provedores ocupados; peço ao Claude Code para tentar de novo em {wait}s")
        return err(429, "Todos os provedores configurados estão no limite agora. Tente novamente em instantes.",
                   "rate_limit_error", {"retry-after": str(wait)})

    def min_wait(self, chain):
        now, best = time.time(), None
        for c in chain:
            prov = self.s.prov(c["provedor"])
            for kid in prov.kids:
                t = max(self.st.cooldown.get(sk(prov.name, kid, c["modelo"]), 0),
                        self.st.dead.get(sk(prov.name, kid), 0)) - now
                if t > 0 and (best is None or t < best):
                    best = t
        return int(best) + 1 if best else 10

    # ------------------------------------------------------------- uma tentativa
    async def attempt(self, prov, ki, cand, body, stream, fwd, est, ctx, shown, ckey):
        """Devolve ("ok", resposta) | ("key", None) tenta outra chave | ("cand", None) próximo modelo."""
        model = cand["modelo"]
        mk = f"{prov.name}/{model}"
        kid = prov.kids[ki]
        if prov.local:
            try:
                await self.local.ensure(cand)
            except RuntimeError as e:
                self.penalize(prov, ki, model, 60, str(e))
                return "cand", None
        self.note_request(prov, kid)
        try:
            resp = await self.send(prov, prov.keys[ki], cand, body, stream, fwd)
        except httpx.HTTPError as e:
            if prov.local:
                self.local.marcar_falha()
            self.penalize(prov, ki, model, 30, f"falha de rede ({type(e).__name__})")
            self.st.problem(mk, "rede")
            return "cand", None

        code = resp.status_code
        if code == 200:
            return await self.on_ok(prov, ki, cand, resp, stream, est, ctx, shown, ckey)

        raw = await resp.aread()
        await resp.aclose()
        detail = raw.decode("utf-8", "replace")[:400]
        baixo = detail.lower()
        if code == 400 and stream and prov.stream_usage and "stream_options" in baixo:
            prov.stream_usage = False
            log(f"[i] {prov.name} não aceita contagem de tokens em stream; seguindo sem ela")
            return "key", None
        if code in (429, 408):
            ra = resp.headers.get("retry-after", "")
            base = float(ra) if re.fullmatch(r"\d+(\.\d+)?", ra or "") else 60
            self.penalize(prov, ki, model, base, "limite de uso atingido (429)")
            self.st.problem(mk, "limite_429")
            return "key", None
        if code in (401, 402, 403):
            self.st.dead[sk(prov.name, kid)] = time.time() + 3600
            self.st.touch()
            self.st.event("chave_desativada", f"{prov.name} (chave {ki + 1}) recusada ou sem saldo (HTTP {code})")
            log(f"[!] {prov.name} (chave {ki + 1}): chave recusada/sem saldo (HTTP {code}) -> desativada por 1h")
            return "key", None
        if code == 404:
            self.penalize(prov, ki, model, 1800, "modelo não encontrado (confira o nome em config.yaml)")
            self.st.problem(mk, "modelo_inexistente")
            return "cand", None
        if code >= 500:
            self.penalize(prov, ki, model, 30, f"erro {code} no provedor")
            self.st.problem(mk, "erro_5xx")
            return "cand", None
        log(f"[!] {prov.name}/{model} recusou o pedido (HTTP {code}): {detail[:200]}")
        self.st.problem(mk, f"http_{code}")
        return "cand", None

    async def on_ok(self, prov, ki, cand, resp, stream, est, ctx, shown, ckey):
        model = cand["modelo"]
        mk = f"{prov.name}/{model}"
        kid = prov.kids[ki]
        info = {}
        dk = sk(prov.name, kid, today())
        self.st.daily[dk] = self.st.daily.get(dk, 0) + 1
        self.st.first_req.setdefault(sk(today(), prov.name), time.time())

        if prov.kind == "anthropic":
            if not stream:
                out = resp.json()
                await resp.aclose()
                u = out.get("usage") or {}
                self.finish(prov, ki, cand, {"usage": {"prompt_tokens": u.get("input_tokens"),
                                                       "completion_tokens": u.get("output_tokens")}}, est, ctx)
                return "ok", JSONResponse(out)

            async def passthrough():
                tin = tout = 0
                try:
                    async for chunk in resp.aiter_raw():
                        for m in re.findall(rb'"input_tokens":\s*(\d+)', chunk):
                            tin = max(tin, int(m))
                        for m in re.findall(rb'"output_tokens":\s*(\d+)', chunk):
                            tout = int(m)
                        yield chunk
                finally:
                    await resp.aclose()
                    self.finish(prov, ki, cand, {"usage": {"prompt_tokens": tin, "completion_tokens": tout}}, est, ctx)
            return "ok", StreamingResponse(passthrough(), media_type="text/event-stream")

        if not stream:
            try:
                out = to_anthropic(resp.json(), shown, est, info)
            except (BadResponse, ValueError, KeyError) as e:
                await resp.aclose()
                self.bad(prov, ki, cand, info, str(e))
                return "cand", None
            await resp.aclose()
            self.finish(prov, ki, cand, info, est, ctx)
            if ckey:
                self.cache.put(ckey, out, self.s.cfg["economia"]["cache_respostas"]["segundos"])
            return "ok", JSONResponse(out)

        try:
            buf, rest = await prime_stream(resp.aiter_lines())
        except BadResponse as e:
            await resp.aclose()
            self.bad(prov, ki, cand, info, str(e))
            return "cand", None
        except httpx.HTTPError as e:
            await resp.aclose()
            if prov.local:
                self.local.marcar_falha()
            self.penalize(prov, ki, cand["modelo"], 30, f"falha de rede no início da resposta ({type(e).__name__})")
            return "cand", None

        async def gen():
            chunks = [] if ckey else None
            try:
                async for ch in translate_stream(_chain(buf, rest), shown, est, info):
                    if chunks is not None:
                        chunks.append(ch)
                    yield ch
            finally:
                try:
                    await resp.aclose()
                except Exception:
                    pass
                if info.get("error"):
                    self.bad(prov, ki, cand, info, info["error"], count_fallback=False)
                else:
                    self.finish(prov, ki, cand, info, est, ctx)
                    if chunks:
                        self.cache.put(ckey, chunks, self.s.cfg["economia"]["cache_respostas"]["segundos"])
        return "ok", StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # ---------------------------------------------------------------- registro
    def bad(self, prov, ki, cand, info, why, count_fallback=True):
        mk = f"{prov.name}/{cand['modelo']}"
        if "ferramenta" in why:
            self.st.problem(mk, "ferramenta_invalida")
        elif "vazia" in why or "sem conteúdo" in why:
            self.st.problem(mk, "resposta_vazia")
        else:
            self.st.problem(mk, "resposta_ruim")
        if info.get("repaired"):
            self.st.problem(mk, "ferramenta_consertada")
        self.penalize(prov, ki, cand["modelo"], 45, why)
        if prov.local:
            self.local.marcar_falha()

    def custo(self, cand, tin, tout):
        p = cand.get("preco_milhao")
        if not p:
            return 0.0
        return (tin * p[0] + tout * p[1]) / 1e6

    def finish(self, prov, ki, cand, info, est, ctx):
        mk = f"{prov.name}/{cand['modelo']}"
        self.st.fails.pop(sk(prov.name, prov.kids[ki], cand["modelo"]), None)
        u = info.get("usage") or {}
        tin = u.get("prompt_tokens") or int(est * self.st.calib.get(mk, 1.0))
        tout = u.get("completion_tokens") or 0
        if u.get("prompt_tokens"):
            self.st.update_calib(mk, est, u["prompt_tokens"])
        self.st.add_tokens(prov.name, cand["modelo"], tin, tout, self.custo(cand, tin, tout))
        if info.get("repaired"):
            self.st.problem(mk, "ferramenta_consertada")
        ctx["sess"].last_model = mk
        self.st.learn_add(ctx["tier"], mk, "n")
        self.st.add_history({
            "nivel": ctx["tier"], "motivo": ctx["why"], "prov": prov.name, "modelo": cand["modelo"],
            "chave": ki + 1, "tin": tin, "tout": tout, "ms": int((time.time() - ctx["t0"]) * 1000),
            "status": "ok", "stream": ctx["stream"], "trocas": ctx["trocas"], "privado": ctx["local_only"],
            "detectado": sorted(ctx["cats"]), "externo_sensivel": ctx["externo_sensivel"] and not prov.local,
            "pesos": ctx["pesos"]})
        log(f"[{ctx['tier']}] -> {mk} (chave {ki + 1})")

    # ----------------------------------------------------------------- consultas
    def sugestoes(self):
        out = []
        for tier, lst in self.s.tiers.items():
            ordem = [f"{c['provedor']}/{c['modelo']}" for c in lst]
            ordem = list(dict.fromkeys(ordem))
            stats = self.st.learn.get(tier, {})

            def taxa(k):
                d = stats.get(k)
                if not d or d.get("n", 0) < 30:
                    return None
                return (d.get("falhas", 0) + d.get("escaladas", 0)) / d["n"]
            for i, ki in enumerate(ordem):
                ti = taxa(ki)
                if ti is None or ti <= 0.25:
                    continue
                for kj in ordem[i + 1:]:
                    tj = taxa(kj)
                    if tj is not None and tj < ti - 0.10:
                        nova = [x for x in ordem if x != kj]
                        nova.insert(nova.index(ki), kj)
                        out.append({"nivel": tier, "mover": kj, "antes_de": ki, "taxa_atual": round(ti, 2),
                                    "taxa_nova": round(tj, 2), "nova_ordem": nova})
                        break
                if out and out[-1]["nivel"] == tier:
                    break
        return out

    def aprovar_ordem(self, tier):
        for s in self.sugestoes():
            if s["nivel"] == tier:
                self.st.ordem[tier] = s["nova_ordem"]
                self.st.touch(True)
                return True
        return False

    def pausar(self, nome, flag):
        if nome not in self.s.providers:
            return False
        (self.st.paused.add if flag else self.st.paused.discard)(nome)
        self.st.touch(True)
        return True

    def liberar_gasto(self, nome):
        p = self.s.prov(nome)
        if not p or not (p.usd_dia or p.usd_mes):
            return False
        extra = (p.usd_dia or p.usd_mes)
        self.st.gasto_extra[nome] = {"data": today(), "usd": extra}
        self.st.touch(True)
        return True

    def status(self):
        now = time.time()
        return {
            "provedores": {n: {"chaves": len(p.keys), "pausado": n in self.st.paused}
                           for n, p in self.s.providers.items()},
            "pedidos_por_modelo": {k.split("|", 1)[1]: v["n"] for k, v in self.st.tokens.items()
                                   if k.startswith(today())},
            "em_pausa": [{"chave": k, "segundos": int(v - now)} for k, v in self.st.cooldown.items() if v > now],
        }

    def alertas(self):
        out = []
        pct = self.s.cfg["alertas"].get("cota_percentual", 80) / 100
        ativos = 0
        now = time.time()
        for n, p in self.s.providers.items():
            if not p.keys or n in self.st.paused:
                continue
            vivas = [k for k in p.kids if self.st.dead.get(sk(n, k), 0) <= now]
            if vivas:
                ativos += 1
            if len(vivas) < len(p.kids):
                out.append({"nivel": "aviso", "msg": f"{n}: {len(p.kids) - len(vivas)} chave(s) desativada(s)"})
            if p.daily_limit:
                usado_frac = 1 - self.pressao(p)
                if usado_frac >= pct:
                    out.append({"nivel": "aviso", "msg": f"{n}: {int(usado_frac * 100)}% da cota diária usada"})
            b = self.gasto_bloqueado(p)
            if b:
                out.append({"nivel": "critico", "msg": f"{n}: limite de gasto do {b} atingido (libere no painel)"})
            else:
                for lim, per in ((p.usd_dia, "dia"), (p.usd_mes, "mes")):
                    if lim and self.st.spent(n, per) >= lim * pct:
                        out.append({"nivel": "aviso", "msg": f"{n}: gasto do {per} passou de {int(pct * 100)}% do limite"})
        if ativos == 1:
            out.append({"nivel": "aviso", "msg": "Só um provedor ativo: se ele falhar, não há reserva"})
        if ativos == 0:
            out.append({"nivel": "critico", "msg": "Nenhum provedor ativo"})
        for a in self.ausentes():
            out.append({"nivel": "aviso", "msg": f"Modelo {a['provedor']}/{a['modelo']} não existe mais"
                        + (f" (parecido: {a['sugestoes'][0]})" if a["sugestoes"] else "")})
        lib = [e for e in self.st.events if e["tipo"] in ("liberado", "privacidade")
               and datetime.fromtimestamp(e["t"]).date().isoformat() == today()]
        if lib:
            out.append({"nivel": "critico", "msg": f"{len(lib)} pedido(s) com dado sensível saíram para a internet hoje"})
        if self.local.estado == "erro":
            out.append({"nivel": "aviso", "msg": "Modelo local com erro: " + (self.local.erro or "")})
        return out

    def painel(self):
        now = time.time()
        d = today()
        provs = []
        for n, p in self.s.providers.items():
            chaves = []
            for i, kid in enumerate(p.kids):
                dead = self.st.dead.get(sk(n, kid), 0) - now
                usado = self.st.daily.get(sk(n, kid, d), 0)
                estado = "ativa"
                if dead > 0:
                    estado = "desativada"
                else:
                    pausas = [v - now for k, v in self.st.cooldown.items()
                              if k.startswith(sk(n, kid) + "|") and v > now]
                    if pausas:
                        estado = "em pausa"
                chaves.append({"n": i + 1, "mascara": "…" + p.keys[i][-4:] if not p.local else "local",
                               "estado": estado, "usado": usado, "limite": p.daily_limit,
                               "segundos": int(max([dead] + [v - now for k, v in self.st.cooldown.items()
                                                             if k.startswith(sk(n, kid) + "|") and v > now]))
                               if estado != "ativa" else 0})
            primeiro = self.st.first_req.get(sk(d, n))
            prev = None
            if p.daily_limit and primeiro and now - primeiro > 600:
                usado_tot = sum(c["usado"] for c in chaves)
                taxa = usado_tot / ((now - primeiro) / 3600)
                resta = p.daily_limit * len(p.keys) - usado_tot
                if taxa > 0:
                    prev = round(max(0.0, resta / taxa), 1)
            provs.append({"nome": n, "pausado": n in self.st.paused, "pago": p.pago, "chaves": chaves,
                          "pressao": round(self.pressao(p), 2), "horas_restantes": prev,
                          "gasto_dia": round(self.st.spent(n, "dia"), 4), "limite_dia": p.usd_dia,
                          "gasto_mes": round(self.st.spent(n, "mes"), 4), "limite_mes": p.usd_mes})
        modelos = []
        tot_in = tot_out = 0
        eco = self.s.cfg["economia"].get("referencia_preco")
        pagos = 0.0
        economia_usd = 0.0
        for k, v in sorted(self.st.tokens.items()):
            data, mk = k.split("|", 1)
            if data != d:
                continue
            modelos.append({"modelo": mk, "pedidos": v["n"], "tin": v["in"], "tout": v["out"],
                            "usd": round(v.get("usd", 0.0), 4)})
            tot_in += v["in"]
            tot_out += v["out"]
            pagos += v.get("usd", 0.0)
            if eco:
                economia_usd += (v["in"] * eco["entrada"] + v["out"] * eco["saida"]) / 1e6
        try:
            avaliacao = json.loads((self.s.data_dir / "avaliacao.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            avaliacao = {}
        hist = list(self.st.history)[-100:][::-1]
        pesos = [h["pesos"] for h in self.st.history if h.get("pesos") and h.get("t", 0) > now - 3600]
        medio = {}
        if pesos:
            for k in pesos[0]:
                medio[k] = int(sum(p.get(k, 0) for p in pesos) / len(pesos))
        hw = self.local.hw or (hardware() if self.s.cfg["local"].get("ativo") else None)
        priv = self.s.cfg["privacidade"]
        hoje = [h for h in self.st.history if datetime.fromtimestamp(h["t"]).date().isoformat() == d]
        return {
            "agora": datetime.now().strftime("%H:%M:%S"), "hoje": d,
            "provedores": provs, "modelos": modelos, "tokens_hoje": {"in": tot_in, "out": tot_out},
            "historico": hist, "alertas": self.alertas(), "sugestoes": self.sugestoes(),
            "ausentes": self.ausentes(), "problemas": self.st.problems, "pesos_medios": medio,
            "economia": ({"referencia": eco, "poupado_usd": round(max(0.0, economia_usd - pagos), 2)} if eco else None),
            "local": {"ativo": bool(self.s.cfg["local"].get("ativo")), "estado": self.local.estado,
                      "erro": self.local.erro, "modelo": Path(self.local.atual).name if self.local.atual else None,
                      "hardware": hw, "recomendacao": recomendar(hw)["texto"] if hw else None},
            "privacidade": {"ativo": bool(priv.get("ativo")),
                            "barrados_hoje": self.blocked_today.get(d, 0),
                            "com_deteccao_hoje": sum(1 for h in hoje if h.get("detectado")),
                            "externos_sensiveis_hoje": sum(1 for h in hoje if h.get("externo_sensivel"))},
            "pedidos_hoje": len(hoje), "avaliacao": avaliacao,
        }

    async def close(self):
        self.local.stop()
        self.st.save()
        await self.client.aclose()
