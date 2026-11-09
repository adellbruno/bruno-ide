# -*- coding: utf-8 -*-
"""Comandos de diagnóstico: --testar, --modelos, --hardware, --chaves, --instalar-llama."""
import getpass
import io
import json
import os
import platform
import re
import stat
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import httpx

from .config import SERVICO_COFRE, BASE
from .local import achar_servidor, gguf_info, hardware, plano_de_carga, recomendar
from .translate import BadResponse, prime_stream, to_anthropic, to_openai, translate_stream, _chain

FERRAMENTA = {"name": "hora_atual", "description": "Devolve a hora atual.",
              "input_schema": {"type": "object", "properties": {"fuso": {"type": "string"}}, "required": ["fuso"]}}


# ------------------------------------------------------------------- --testar
async def _um_teste(engine, prov, cand, nome, body, stream):
    key = prov.keys[0]
    try:
        resp = await engine.send(prov, key, cand, body, stream, {})
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if resp.status_code != 200:
        txt = (await resp.aread()).decode("utf-8", "replace")[:160]
        await resp.aclose()
        return False, f"HTTP {resp.status_code}: {txt}"
    info = {}
    try:
        if prov.kind == "anthropic":
            await resp.aread()
            await resp.aclose()
            return True, "ok"
        if not stream:
            out = to_anthropic(resp.json(), cand["modelo"], 10, info)
            await resp.aclose()
            if nome == "ferramenta" and not any(b["type"] == "tool_use" for b in out["content"]):
                return False, "não chamou a ferramenta"
            return True, "ok" + (" (JSON consertado)" if info.get("repaired") else "")
        buf, rest = await prime_stream(resp.aiter_lines())
        eventos = []
        async for ch in translate_stream(_chain(buf, rest), cand["modelo"], 10, info):
            eventos.append(ch)
        await resp.aclose()
        if info.get("error"):
            return False, info["error"]
        return True, "ok"
    except BadResponse as e:
        await resp.aclose()
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def rodar(engine, testar=False, modelos=False):
    s = engine.s
    if modelos:
        await engine.check_models()
        print("\nConferência de modelos (compara o config.yaml com o que cada provedor oferece)\n")
        for nome, prov in s.providers.items():
            if prov.local or not prov.keys:
                continue
            ids = engine.avail.get(nome)
            print(f"  {nome}: " + (f"{len(ids)} modelos disponíveis" if ids else "não consegui listar (sem checagem)"))
        aus = engine.ausentes()
        if not aus:
            print("\n  Todos os modelos do config.yaml existem.")
        for a in aus:
            sug = ", ".join(a["sugestoes"]) or "nenhum parecido encontrado"
            print(f"\n  NÃO EXISTE: {a['provedor']}/{a['modelo']} (nível {a['nivel']})\n    parecidos: {sug}")
        print("\nDica: verificar_modelos: pular no config.yaml faz o roteador ignorar sozinho os que sumiram.")
    if not testar:
        return
    ativos = [n for n, p in s.providers.items() if p.keys and not p.local]
    if not ativos:
        print("Nenhuma chave encontrada. Rode: python app.py --chaves")
        return
    print("\nTestando cada modelo com a primeira chave de cada provedor (texto, ferramenta e streaming)\n")
    vistos = set()
    falhas = 0
    for tier, lst in s.tiers.items():
        for c in lst:
            prov = s.prov(c["provedor"])
            k = (c["provedor"], c["modelo"])
            if not prov or not prov.keys or prov.local or k in vistos:
                continue
            vistos.add(k)
            base = {"max_tokens": 64, "messages": [{"role": "user", "content": "Responda apenas: ok"}]}
            ferr = {"max_tokens": 128, "tools": [FERRAMENTA], "tool_choice": {"type": "any"},
                    "messages": [{"role": "user", "content": "Use a ferramenta hora_atual com fuso America/Sao_Paulo."}]}
            partes = []
            for nome, body, stream in (("texto", base, False), ("ferramenta", ferr, False), ("streaming", base, True)):
                ok, msg = await _um_teste(engine, prov, c, nome, body, stream)
                partes.append(f"{nome}: {'OK' if ok else 'FALHA'}")
                if not ok:
                    falhas += 1
                    partes[-1] += f" ({msg[:90]})"
            print(f"  {prov.name}/{c['modelo']}\n    " + " | ".join(partes))
    print("\n" + ("Tudo certo." if not falhas else
                  "Há falhas: ajuste o nome do modelo no config.yaml ou marque 'tools: false' se ele não chama ferramentas."))
    await engine.client.aclose()


# ---------------------------------------------------------------- --hardware
def mostrar_hardware(settings):
    hw = hardware()
    r = recomendar(hw)
    print("\nSeu computador")
    print(f"  Sistema: {hw['so']}   Núcleos: {hw['cpus']}   RAM: {hw['ram_gb']} GB")
    print(f"  Placa de vídeo: {hw['gpu'] or 'não detectada (só NVIDIA e Apple Silicon são reconhecidas)'}"
          + (f" com {hw['vram_gb']} GB" if hw["gpu"] else ""))
    print("\n  " + r["texto"])
    exe = achar_servidor(settings.cfg["local"])
    print(f"\n  llama-server: {exe or 'não encontrado (rode: python app.py --instalar-llama)'}")
    pasta = Path(settings.cfg["local"].get("pasta_modelos") or "modelos")
    pasta = pasta if pasta.is_absolute() else settings.base / pasta
    arquivos = sorted(pasta.glob("*.gguf")) if pasta.exists() else []
    print(f"\n  Pasta de modelos: {pasta}  ({len(arquivos)} arquivo(s) .gguf)")
    for a in arquivos:
        info = gguf_info(a)
        if not info:
            print(f"   - {a.name}: não parece um GGUF válido")
            continue
        plano = plano_de_carga({"arquivo": str(a), "contexto": 8192, "gpu_camadas": "auto"}, hw)
        onde = "tudo na placa de vídeo" if plano["ngl"] >= plano["camadas"] else (
            f"{plano['ngl']} de {plano['camadas']} camadas na placa de vídeo" if plano["ngl"] else "só no processador")
        print(f"   - {a.name}: {info['tamanho_gb']} GB, {info.get('camadas', '?')} camadas, "
              f"contexto até {info.get('contexto_max', '?')}; rodaria {onde}, contexto {plano['ctx']}")


# ----------------------------------------------------------------- --chaves
def _gravar_env(env_path, var, valor):
    linhas = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    novo, feito = [], False
    for ln in linhas:
        if re.match(rf"^\s*{re.escape(var)}\s*=", ln):
            novo.append(f"{var}={valor}")
            feito = True
        else:
            novo.append(ln)
    if not feito:
        novo.append(f"{var}={valor}")
    env_path.write_text("\n".join(novo) + "\n", encoding="utf-8")


def assistente_chaves(settings):
    cofre = None
    try:
        import keyring  # type: ignore

        keyring.get_password(SERVICO_COFRE, "teste")  # confirma que há um cofre funcionando
        cofre = keyring
    except Exception:
        cofre = None
    print("\nAssistente de chaves. Cole a chave e tecle Enter (nada aparece na tela). Enter vazio = pular.")
    print("Mais de uma chave do mesmo serviço? Separe por vírgula.")
    print("Guardando em: " + ("cofre do sistema" if cofre else "arquivo .env (instale 'keyring' para usar o cofre)") + "\n")
    cfg = settings.cfg.get("provedores") or {}
    for nome, c in cfg.items():
        var = c.get("variavel_env")
        if not var:
            continue
        v = getpass.getpass(f"  {nome} ({var}): ").strip()
        if not v:
            continue
        if cofre:
            try:
                cofre.set_password(SERVICO_COFRE, var, v)
                continue
            except Exception:
                pass
        _gravar_env(settings.env_file, var, v)
    print("\nPronto. Rode: python app.py --testar")


# --------------------------------------------------------- --instalar-llama
def instalar_llama(settings):
    """Baixa o llama-server do projeto llama.cpp (melhor esforço: depende da rede e dos nomes dos arquivos)."""
    destino = BASE / "llama.cpp"
    sistema = platform.system()
    maq = platform.machine().lower()
    hw = hardware()
    print("Procurando a versão mais recente do llama.cpp no GitHub...")
    try:
        r = httpx.get("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest", timeout=30,
                      follow_redirects=True)
        r.raise_for_status()
        assets = r.json()["assets"]
    except Exception as e:
        print(f"Não consegui acessar o GitHub ({e}).\nBaixe à mão em https://github.com/ggml-org/llama.cpp/releases, "
              f"extraia em {destino} e rode de novo.")
        return
    nomes = [a["name"] for a in assets]
    if sistema == "Windows":
        prefer = (["bin-win-cuda", "x64"] if hw.get("gpu") and "NVIDIA" in (hw["gpu"] or "").upper()
                  else ["bin-win-vulkan", "x64"])
        fallback = ["bin-win-cpu", "x64"]
    elif sistema == "Darwin":
        prefer = ["bin-macos-arm64"] if "arm" in maq else ["bin-macos-x64"]
        fallback = prefer
    else:
        prefer, fallback = ["bin-ubuntu", "x64"], ["bin-ubuntu", "x64"]
    escolhido = None
    for chave in (prefer, fallback):
        for a in assets:
            n = a["name"].lower()
            if all(k in n for k in chave) and n.endswith((".zip", ".tar.gz")) and "cudart" not in n:
                escolhido = a
                break
        if escolhido:
            break
    if not escolhido:
        print("Não achei um pacote para o seu sistema. Arquivos disponíveis:\n  " + "\n  ".join(nomes[:25]))
        return
    print(f"Baixando {escolhido['name']} ({escolhido['size'] // 1024 // 1024} MB)...")
    try:
        dados = httpx.get(escolhido["browser_download_url"], timeout=600, follow_redirects=True).content
    except Exception as e:
        print(f"Falhou o download: {e}")
        return
    destino.mkdir(exist_ok=True)
    if escolhido["name"].endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            z.extractall(destino)
    else:
        with tarfile.open(fileobj=io.BytesIO(dados)) as t:
            t.extractall(destino, filter="data") if sys.version_info >= (3, 12) else t.extractall(destino)
    for p in destino.rglob("llama-server*"):
        if p.is_file() and not p.suffix in (".dll", ".so"):
            try:
                p.chmod(p.stat().st_mode | stat.S_IEXEC)
            except OSError:
                pass
    achou = achar_servidor({"servidor": ""}) or next((str(p) for p in destino.rglob("llama-server*") if p.is_file()), None)
    print(f"Pronto: {achou}" if achou else "Extraído, mas não achei o llama-server. Indique o caminho em local.servidor.")
    if achou and str(destino) not in achou:
        print(f"Se o roteador não o encontrar, ponha em local.servidor: {achou}")
