# -*- coding: utf-8 -*-
"""Estado que sobrevive a reiniciar: pausas, cotas, tokens, aprendizado, histórico."""
import json
import os
import time
from collections import deque
from datetime import date, timedelta
from pathlib import Path


def today():
    return date.today().isoformat()


def month():
    return date.today().strftime("%Y-%m")


def sk(*parts):
    return "|".join(str(p) for p in parts)


class State:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "estado.json"
        self.hist_path = self.dir / "historico.jsonl"
        self.cooldown = {}   # "prov|kid|modelo" -> até quando
        self.fails = {}      # falhas seguidas (não persiste)
        self.dead = {}       # "prov|kid" -> até quando
        self.paused = set()  # provedores pausados à mão
        self.daily = {}      # "prov|kid|data" -> pedidos
        self.tokens = {}     # "data|prov/modelo" -> {n, in, out, usd}
        self.calib = {}      # "prov/modelo" -> fator real/estimado
        self.problems = {}   # "prov/modelo" -> contadores
        self.learn = {}      # nivel -> "prov/modelo" -> {n, falhas, escaladas}
        self.gasto = {}      # "data" -> {prov: usd}
        self.gasto_extra = {}  # prov -> {"data": ..., "usd": ...}
        self.ordem = {}      # nivel -> ["prov/modelo", ...] aprovada
        self.first_req = {}  # "data|prov" -> epoch do primeiro pedido do dia
        self.rpm = {}        # (prov, kid) -> deque de horários (não persiste)
        self.history = deque(maxlen=500)
        self.events = deque(maxlen=200)
        self._dirty = False
        self._last = 0.0
        self.load()

    # ----------------------------------------------------------------- arquivo
    def load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        now = time.time()
        self.cooldown = {k: v for k, v in d.get("cooldown", {}).items() if v > now}
        self.dead = {k: v for k, v in d.get("dead", {}).items() if v > now}
        self.paused = set(d.get("paused", []))
        corte = (date.today() - timedelta(days=3)).isoformat()
        self.daily = {k: v for k, v in d.get("daily", {}).items() if k.rsplit("|", 1)[-1] >= corte}
        corte_t = (date.today() - timedelta(days=62)).isoformat()
        self.tokens = {k: v for k, v in d.get("tokens", {}).items() if k.split("|", 1)[0] >= corte_t}
        self.calib = d.get("calib", {})
        self.problems = d.get("problems", {})
        self.learn = d.get("learn", {})
        self.gasto = {k: v for k, v in d.get("gasto", {}).items() if k >= corte_t}
        self.gasto_extra = d.get("gasto_extra", {})
        self.ordem = d.get("ordem", {})
        self.first_req = {k: v for k, v in d.get("first_req", {}).items() if k.split("|", 1)[0] >= corte}
        try:
            linhas = self.hist_path.read_text(encoding="utf-8").splitlines()[-500:]
            for ln in linhas:
                try:
                    self.history.append(json.loads(ln))
                except ValueError:
                    pass
        except OSError:
            pass

    def snapshot(self):
        return {
            "cooldown": self.cooldown,
            "dead": self.dead,
            "paused": sorted(self.paused),
            "daily": self.daily,
            "tokens": self.tokens,
            "calib": self.calib,
            "problems": self.problems,
            "learn": self.learn,
            "gasto": self.gasto,
            "gasto_extra": self.gasto_extra,
            "ordem": self.ordem,
            "first_req": self.first_req,
        }

    def save(self):
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            self._dirty = False
            self._last = time.time()
        except OSError:
            pass

    def touch(self, force=False):
        """Marca como alterado e grava no máximo a cada 3 s."""
        self._dirty = True
        if force or time.time() - self._last > 3:
            self.save()

    # ---------------------------------------------------------------- histórico
    def add_history(self, entry):
        """Guarda só metadados: nunca o texto do pedido nem da resposta."""
        entry = {"t": time.time(), **entry}
        self.history.append(entry)
        try:
            if self.hist_path.exists() and self.hist_path.stat().st_size > 5_000_000:
                linhas = self.hist_path.read_text(encoding="utf-8").splitlines()[-2000:]
                self.hist_path.write_text("\n".join(linhas) + "\n", encoding="utf-8")
            with open(self.hist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def event(self, kind, msg):
        self.events.append({"t": time.time(), "tipo": kind, "msg": msg})

    # -------------------------------------------------------------- contadores
    def add_tokens(self, prov, model, tin, tout, usd=0.0):
        k = sk(today(), f"{prov}/{model}")
        d = self.tokens.setdefault(k, {"n": 0, "in": 0, "out": 0, "usd": 0.0})
        d["n"] += 1
        d["in"] += int(tin or 0)
        d["out"] += int(tout or 0)
        d["usd"] = round(d.get("usd", 0.0) + usd, 6)
        if usd:
            g = self.gasto.setdefault(today(), {})
            g[prov] = round(g.get(prov, 0.0) + usd, 6)
        self.touch()

    def spent(self, prov, period="dia"):
        if period == "dia":
            return self.gasto.get(today(), {}).get(prov, 0.0)
        m = month()
        return sum(v.get(prov, 0.0) for k, v in self.gasto.items() if k.startswith(m))

    def problem(self, model_key, kind):
        d = self.problems.setdefault(model_key, {})
        d[kind] = d.get(kind, 0) + 1
        self.touch()

    def learn_add(self, tier, model_key, field):
        d = self.learn.setdefault(tier, {}).setdefault(model_key, {"n": 0, "falhas": 0, "escaladas": 0})
        d[field] = d.get(field, 0) + 1
        self.touch()

    def update_calib(self, model_key, estimated, real):
        if not estimated or not real:
            return
        r = max(0.3, min(3.0, real / estimated))
        old = self.calib.get(model_key)
        self.calib[model_key] = round(r if old is None else 0.8 * old + 0.2 * r, 4)
