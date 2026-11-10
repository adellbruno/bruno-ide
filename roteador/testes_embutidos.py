# -*- coding: utf-8 -*-
"""Testes do llama.cpp embutido: escolha da variante, modo local 'auto' e lista automática de .gguf.
Rodar: python testes_embutidos.py   (não precisa de rede nem de GPU)"""
import os, sys, tempfile, types
from pathlib import Path

try:
    import httpx  # noqa: F401
except ImportError:  # ambiente de teste enxuto: o módulo só é usado em chamadas de rede
    sys.modules["httpx"] = types.ModuleType("httpx")
    sys.modules["httpx"].HTTPError = Exception

tmp = Path(tempfile.mkdtemp())
os.environ["ROTEADOR_HOME"] = str(tmp / "home")
from rot import local  # noqa: E402
from rot.config import Settings  # noqa: E402

exe = local._exe_nome()
emb = tmp / "llama.cpp"
for v in ("cuda", "vulkan", "cpu"):
    (emb / v).mkdir(parents=True)
    (emb / v / exe).write_text("x")
os.environ["BRUNO_LLAMA_DIR"] = str(emb)

nvidia = {"gpu": "NVIDIA GeForce RTX 3060", "vram_gb": 12.0}
semgpu = {"gpu": None, "vram_gb": 0.0}
nomes = lambda lst: [Path(p).parent.name for p in lst][:3]
assert nomes(local.achar_servidores({}, nvidia)) == ["cuda", "vulkan", "cpu"]
assert nomes(local.achar_servidores({}, semgpu)) == ["vulkan", "cpu", "cuda"]
assert nomes(local.achar_servidores({"variante": "cpu"}, nvidia)) == ["cpu", "cuda", "vulkan"]
outro = tmp / "meu" / exe
outro.parent.mkdir()
outro.write_text("x")
assert local.achar_servidores({"servidor": str(outro)}, nvidia)[0] == str(outro)  # o caminho do usuário manda
os.environ.pop("BRUNO_LLAMA_DIR")
assert not any(str(emb) in p for p in local.achar_servidores({}, nvidia))
print("ok: escolha de variante")

def settings(extra=""):
    cfgp = tmp / "config.yaml"
    cfgp.write_text("provedores: {}\nniveis: {simples: [], medio: [], complexo: []}\nlocal:\n  pasta_modelos: modelos\n" + extra, encoding="utf-8")
    return Settings(config_path=cfgp, data_dir=tmp / "dados")

s = settings("  ativo: auto\n")
assert s.cfg["local"]["ativo"] is False and not s.local_cands
(s.base / "modelos").mkdir(exist_ok=True)
(s.base / "modelos" / "a.gguf").write_bytes(b"GGUF")
(s.base / "modelos" / "b.txt").write_text("nao")
s = settings("  ativo: auto\n")
assert s.cfg["local"]["ativo"] is True
assert [c["modelo"] for c in s.local_cands] == ["a.gguf"] and s.local_cands[0]["contexto"] == 32768
assert settings("  ativo: false\n").local_cands == []
assert settings("  ativo: true\n").cfg["local"]["ativo"] is True
# colocar um .gguf novo muda o carimbo e dispara a recarga sozinha
antes = s._stamp()
(s.base / "modelos" / "c.gguf").write_bytes(b"GGUF")
assert s._stamp() != antes
print("ok: modo auto, lista automática e recarga")
print("TESTES EMBUTIDOS OK")
