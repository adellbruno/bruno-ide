# -*- coding: utf-8 -*-
"""Leitura do config.yaml, das chaves e montagem dos provedores."""
import copy
import hashlib
import os
import re
import shutil
import time
from pathlib import Path

import yaml

def _descobrir_base():
    """Pasta de trabalho do roteador. Instalado no editor (executável) ou com ROTEADOR_HOME, usa uma
    pasta do usuário (config.yaml, .env, dados, modelos); no modo normal, a própria pasta do projeto."""
    import os
    import shutil
    import sys

    casa = os.getenv("ROTEADOR_HOME")
    empacotado = getattr(sys, "frozen", False)
    if not casa and not empacotado:
        return Path(__file__).resolve().parent.parent
    if not casa:
        raiz = os.getenv("APPDATA") if sys.platform.startswith("win") else os.path.join(os.path.expanduser("~"), ".config")
        casa = os.path.join(raiz or os.path.expanduser("~"), "BrunoIDE", "roteador")
    base = Path(casa)
    base.mkdir(parents=True, exist_ok=True)
    cfg = base / "config.yaml"
    if not cfg.exists():
        for origem in (Path(getattr(sys, "_MEIPASS", "")) / "config.yaml", Path(__file__).resolve().parent.parent / "config.yaml"):
            if origem.is_file():
                shutil.copy(origem, cfg)
                break
    env = base / ".env"
    if not env.exists():
        for origem in (Path(getattr(sys, "_MEIPASS", "")) / ".env.example", Path(__file__).resolve().parent.parent / ".env.example"):
            if origem.is_file():
                shutil.copy(origem, env)
                break
    return base


BASE = _descobrir_base()
SERVICO_COFRE = "roteador-ia"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def key_id(key):
    """Identificador curto e irreversível de uma chave (nunca guardamos a chave)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def mask(key):
    return ("…" + key[-4:]) if len(key) > 8 else "…"


DEFAULTS = {
    "porta": 8082,
    "dados": "dados",
    "seguranca": {"token_local": True},
    "privacidade": {
        "ativo": True,
        "detectar": ["processo", "cpf", "cnpj", "conta", "telefone"],
        "pastas_protegidas": [],
        "sem_local": "bloquear",  # bloquear | avisar (avisar = deixa sair, só registra)
    },
    "local": {
        "ativo": False,
        "uso": "reserva",  # reserva | principal | so_privado
        "servidor": "",
        "pasta_modelos": "modelos",
        "porta": 8091,
        "espera_s": 180,
        "modelos": [],
    },
    "economia": {
        "modo": "desligado",  # desligado | leve | forte | personalizado
        "cortar_resultados": {"ativo": False, "antigos_chars": 12000, "recente_chars": 40000},
        "ferramentas_enxutas": {"ativo": False, "max": 18},
        "compactar_conversa": {"ativo": False, "a_partir_de_tokens": 60000, "manter_ultimas": 10},
        "cache_respostas": {"ativo": False, "segundos": 300},
        "referencia_preco": None,  # {entrada: 3.0, saida: 15.0}  (US$ por milhão, informe você)
    },
    "roteamento": {
        "atalhos": {"#rapido": "simples", "#medio": "medio", "#pro": "complexo"},
        "medio_a_partir_de": 1,
        "complexo_a_partir_de": 3,
        "palavras_complexas": ["refator", "arquitetur", "bug", "debug", "erro", "traceback", "exception",
                               "otimiz", "performance", "segurança", "migra", "concorrên", "race condition",
                               "refactor", "architecture", "design", "algoritmo", "não funciona", "nao funciona"],
        "palavras_simples": ["liste", "listar", "explique", "o que faz", "leia", "abra", "renomeie", "comente",
                             "traduza", "git status", "resuma", "formate"],
        "escalada": {"ativo": True, "falhas_seguidas": 2, "descer_apos_sucessos": 6},
        "classificador_llm": {"ativo": False, "timeout_s": 8},
        "distribuir_carga": True,
        "cota_baixa_abaixo_de": 0.25,
    },
    "verificar_modelos": "avisar",  # avisar | pular | desligado
    "alertas": {"cota_percentual": 80},
}


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _split(v):
    return [k for k in re.split(r"[,;\s]+", v or "") if k]


def load_keys(var, env):
    keys = []
    try:  # cofre do sistema (opcional)
        import keyring  # type: ignore

        keys += _split(keyring.get_password(SERVICO_COFRE, var))
    except Exception:
        pass
    keys += _split(env.get(var, ""))
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


class Provider:
    def __init__(self, name, c, keys):
        self.name = name
        self.kind = c.get("tipo", "openai")
        self.base_url = c["base_url"].rstrip("/")
        self.keys = keys
        self.kids = [key_id(k) for k in keys]
        self.daily_limit = c.get("limite_diario")
        self.rpm = c.get("limite_por_minuto")
        self.headers = c.get("cabecalhos") or {}
        self.pago = bool(c.get("pago", False))
        self.usd_dia = c.get("limite_gasto_dia_usd")
        self.usd_mes = c.get("limite_gasto_mes_usd")
        self.stream_usage = bool(c.get("usage_em_stream", True))
        self.local = bool(c.get("local", False))
        self.rr = 0


class Settings:
    def __init__(self, config_path=None, data_dir=None, env=None, env_file=None):
        self.config_path = Path(config_path or os.getenv("ROTEADOR_CONFIG") or BASE / "config.yaml")
        self.base = self.config_path.parent
        self.env_file = Path(env_file) if env_file else self.base / ".env"
        self._env_override = env
        self._data_dir_override = data_dir
        self._mtimes = None
        self._checked = 0.0
        self.providers = {}
        self.tiers = {}
        self.local_cands = []
        self.cfg = {}
        self.load()

    # ---------------------------------------------------------------- ambiente
    def _env(self):
        if self._env_override is not None:
            return dict(self._env_override)
        env = {}
        try:
            from dotenv import dotenv_values

            if self.env_file.exists():
                env.update({k: v for k, v in dotenv_values(self.env_file).items() if v})
        except Exception:
            pass
        env.update(os.environ)
        return env

    def _stamp(self):
        out = []
        for p in (self.config_path, self.env_file):
            try:
                out.append(p.stat().st_mtime)
            except OSError:
                out.append(0)
        return tuple(out)

    # ------------------------------------------------------------------- carga
    def load(self):
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        cfg = deep_merge(DEFAULTS, raw)
        eco = cfg["economia"]
        modo = eco.get("modo", "desligado")
        if modo == "leve":
            eco["cortar_resultados"]["ativo"] = True
        elif modo == "forte":
            eco["cortar_resultados"]["ativo"] = True
            eco["ferramentas_enxutas"]["ativo"] = True
            eco["compactar_conversa"]["ativo"] = True
        env = self._env()

        providers = {}
        for name, c in (cfg.get("provedores") or {}).items():
            providers[name] = Provider(name, c, load_keys(c.get("variavel_env", ""), env))

        local = cfg["local"]
        local_cands = []
        if local.get("ativo"):
            porta = int(local.get("porta", 8091))
            providers["local"] = Provider(
                "local",
                {"base_url": f"http://127.0.0.1:{porta}/v1", "local": True, "usage_em_stream": True},
                ["local"],
            )
            pasta = Path(local.get("pasta_modelos") or "modelos")
            if not pasta.is_absolute():
                pasta = self.base / pasta
            lista = list(local.get("modelos") or [])
            if not lista and pasta.is_dir():  # sem lista no config: usa todo .gguf que estiver na pasta
                lista = [{"arquivo": f.name, "nivel": local.get("nivel_padrao", "simples"),
                          "contexto": int(local.get("contexto_padrao", 16384))} for f in sorted(pasta.glob("*.gguf"))]
            for m in lista:
                arq = Path(m["arquivo"])
                if not arq.is_absolute():
                    arq = pasta / arq
                local_cands.append(
                    {
                        "provedor": "local",
                        "modelo": Path(m["arquivo"]).name,
                        "arquivo": str(arq),
                        "nivel": m.get("nivel", "simples"),
                        "contexto": int(m.get("contexto", 8192)),
                        "max_tokens": int(m.get("max_tokens", 4096)),
                        "gpu_camadas": m.get("gpu_camadas", "auto"),
                        "tools": m.get("tools", True),
                        "gratis": True,
                        "local": True,
                    }
                )

        dados = Path(self._data_dir_override or cfg.get("dados") or "dados")
        if not dados.is_absolute():
            dados = self.base / dados
        dados.mkdir(parents=True, exist_ok=True)

        self.cfg = cfg
        self.providers = providers
        self.local_cands = local_cands
        self.tiers = {t: list(v or []) for t, v in (cfg.get("niveis") or {}).items()}
        self.data_dir = dados
        self._mtimes = self._stamp()
        self._backup()

    def reload_if_changed(self):
        """Recarrega o config.yaml e o .env se mudaram (no máximo a cada 2 s)."""
        now = time.time()
        if now - self._checked < 2:
            return False
        self._checked = now
        stamp = self._stamp()
        if stamp == self._mtimes:
            return False
        try:
            old = {n: p for n, p in self.providers.items()}
            self.load()
            for n, p in self.providers.items():  # preserva o rodízio
                if n in old:
                    p.rr = old[n].rr
            log("[config] configuração recarregada")
            return True
        except Exception as e:  # config quebrado: mantém o anterior
            self._mtimes = stamp
            log(f"[!] config.yaml com erro, mantendo a versão anterior: {e}")
            return False

    def _backup(self):
        """Copia o config.yaml (sem chaves) a cada alteração; guarda as 20 últimas."""
        try:
            pasta = self.data_dir / "backups"
            pasta.mkdir(exist_ok=True)
            h = hashlib.sha1(self.config_path.read_bytes()).hexdigest()[:10]
            if any(h in p.name for p in pasta.glob("config-*.yaml")):
                return
            shutil.copy(self.config_path, pasta / f"config-{time.strftime('%Y%m%d-%H%M%S')}-{h}.yaml")
            for velho in sorted(pasta.glob("config-*.yaml"))[:-20]:
                velho.unlink()
        except Exception:
            pass

    # --------------------------------------------------------------- utilidades
    def prov(self, name):
        return self.providers.get(name)
