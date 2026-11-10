# -*- coding: utf-8 -*-
"""Modelos locais em arquivo GGUF: leitura do arquivo, detecção do computador e llama-server."""
import asyncio
import ctypes
import os
import platform
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .config import BASE, log

GB = 1024 ** 3


# ------------------------------------------------------------------- arquivo GGUF
_FMT = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def _read_str(f):
    (n,) = struct.unpack("<Q", f.read(8))
    return f.read(n).decode("utf-8", "replace")


def _read_val(f, t):
    if t in _FMT:
        fmt = _FMT[t]
        return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]
    if t == 8:
        return _read_str(f)
    if t == 9:
        (et,) = struct.unpack("<I", f.read(4))
        (n,) = struct.unpack("<Q", f.read(8))
        if et in _FMT:  # arrays numéricos: pula de uma vez
            f.seek(n * struct.calcsize(_FMT[et]), 1)
            return None
        for _ in range(n):
            _read_val(f, et)
        return None
    raise ValueError(f"tipo GGUF desconhecido: {t}")


def gguf_info(path):
    """Lê o cabeçalho do .gguf: arquitetura, camadas, contexto máximo, tamanho em GB.

    Devolve {} se o arquivo não for um GGUF válido.
    """
    p = Path(path)
    info = {"arquivo": p.name, "tamanho_gb": round(p.stat().st_size / GB, 2) if p.exists() else 0}
    try:
        with open(p, "rb") as f:
            if f.read(4) != b"GGUF":
                return {}
            (_ver,) = struct.unpack("<I", f.read(4))
            _tensores, kv = struct.unpack("<QQ", f.read(16))
            arch = None
            for _ in range(min(kv, 400)):
                chave = _read_str(f)
                (t,) = struct.unpack("<I", f.read(4))
                v = _read_val(f, t)
                if chave == "general.architecture":
                    arch = v
                    info["arquitetura"] = v
                elif chave == "general.name":
                    info["nome"] = v
                elif arch and chave == f"{arch}.block_count":
                    info["camadas"] = int(v)
                elif arch and chave == f"{arch}.context_length":
                    info["contexto_max"] = int(v)
                elif chave == "general.file_type":
                    info["tipo_arquivo"] = int(v)
                if "camadas" in info and "contexto_max" in info:
                    break
    except (OSError, ValueError, struct.error):
        return {}
    return info


# -------------------------------------------------------------------- hardware
def _ram_gb():
    try:
        if sys.platform.startswith("win"):
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
            return round(m.ullTotalPhys / GB, 1)
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return round(int(out.stdout.strip()) / GB, 1)
        with open("/proc/meminfo", encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("MemTotal:"):
                    return round(int(ln.split()[1]) / 1024 / 1024, 1)
    except Exception:
        pass
    return None


def hardware():
    """Descobre RAM e placa de vídeo. Só enxerga NVIDIA (nvidia-smi) e Apple Silicon."""
    hw = {"so": platform.system(), "ram_gb": _ram_gb(), "cpus": os.cpu_count(), "gpu": None, "vram_gb": 0.0}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8)
        if out.returncode == 0 and out.stdout.strip():
            nome, mem = out.stdout.strip().splitlines()[0].split(",")
            hw["gpu"], hw["vram_gb"] = nome.strip(), round(float(mem) / 1024, 1)
    except Exception:
        pass
    if hw["gpu"] is None and sys.platform == "darwin" and platform.machine() == "arm64" and hw["ram_gb"]:
        hw["gpu"], hw["vram_gb"] = "Apple Silicon (memória unificada)", round(hw["ram_gb"] * 0.65, 1)
    return hw


def recomendar(hw):
    """Tamanho de modelo que cabe, em português simples."""
    if hw.get("vram_gb"):
        orcamento = hw["vram_gb"] * 0.85
        onde = f"na placa de vídeo ({hw['gpu']}, {hw['vram_gb']} GB)"
    elif hw.get("ram_gb"):
        orcamento = hw["ram_gb"] * 0.45
        onde = f"na memória RAM ({hw['ram_gb']} GB), sem placa de vídeo compatível"
    else:
        return {"orcamento_gb": None, "bilhoes": None, "texto": "Não consegui medir o seu computador."}
    bilhoes = max(1, int((orcamento - 1.0) / 0.62))  # ~0,62 GB por bilhão de parâmetros em Q4
    lento = "" if hw.get("vram_gb") else " Sem placa de vídeo, espere respostas mais lentas (dezenas de segundos)."
    texto = (f"Seu computador roda bem modelos de até cerca de {bilhoes} bilhões de parâmetros "
             f"(arquivo .gguf de uns {orcamento - 1.0:.0f} GB, quantização Q4) {onde}.{lento}")
    return {"orcamento_gb": round(orcamento, 1), "bilhoes": bilhoes, "texto": texto}


def plano_de_carga(cand, hw):
    """Escolhe camadas na GPU e contexto para um modelo."""
    info = gguf_info(cand["arquivo"])
    tam = info.get("tamanho_gb") or 4.0
    camadas = info.get("camadas") or 32
    ctx = int(cand.get("contexto") or 8192)
    if info.get("contexto_max"):
        ctx = min(ctx, info["contexto_max"])
    gpu = cand.get("gpu_camadas", "auto")
    if gpu == "auto":
        v = hw.get("vram_gb") or 0
        if not v:
            ngl = 0
        elif tam + 1.0 <= v * 0.85:
            ngl = 999
        else:
            ngl = max(0, int(camadas * (v * 0.8) / max(tam, 0.1)))
    else:
        ngl = int(gpu)
    ram = hw.get("ram_gb")
    if ram and ram < 12:
        ctx = min(ctx, 8192)
    return {"ngl": ngl, "ctx": ctx, "camadas": camadas, "tamanho_gb": tam}


def _exe_nome():
    return "llama-server.exe" if sys.platform.startswith("win") else "llama-server"


def ordem_variantes(hw, preferida="auto"):
    """Ordem de tentativa das variantes embutidas do llama.cpp (pastas cuda, vulkan, cpu)."""
    todas = ["cuda", "vulkan", "cpu"]
    if preferida in todas:
        return [preferida] + [v for v in todas if v != preferida]
    if "NVIDIA" in ((hw or {}).get("gpu") or "").upper():
        return ["cuda", "vulkan", "cpu"]
    return ["vulkan", "cpu", "cuda"]  # AMD/Intel/sem placa: Vulkan (cai para CPU sozinho), depois CPU


def achar_servidores(cfg_local, hw=None):
    """Todos os llama-server utilizáveis, do mais indicado ao menos.

    Ordem: caminho em local.servidor, variantes embutidas no instalador (BRUNO_LLAMA_DIR, escolhidas pela
    placa de vídeo), pasta llama.cpp baixada por --instalar-llama e, por fim, o que estiver no PATH.
    """
    exe = _exe_nome()
    achados = []

    def add(p):
        p = Path(p)
        if p.is_file() and str(p) not in achados:
            achados.append(str(p))

    if cfg_local.get("servidor"):
        add(cfg_local["servidor"])
    emb = os.environ.get("BRUNO_LLAMA_DIR")
    if emb and Path(emb).is_dir():
        hw = hw if hw is not None else hardware()
        for v in ordem_variantes(hw, str(cfg_local.get("variante", "auto")).lower()):
            d = Path(emb) / v
            if (d / exe).is_file():
                add(d / exe)
            elif d.is_dir():
                for p in sorted(d.rglob(exe)):
                    add(p)
        if (Path(emb) / exe).is_file():  # pacote solto, sem subpastas de variante
            add(Path(emb) / exe)
    for pasta in (BASE / "llama.cpp", BASE / "llama", BASE / "bin", BASE):
        add(pasta / exe)
    pasta = BASE / "llama.cpp"  # o pacote baixado costuma ter subpastas
    if pasta.exists():
        for p in sorted(pasta.rglob(exe)):
            add(p)
    no_path = shutil.which("llama-server")
    if no_path:
        add(no_path)
    return achados


def achar_servidor(cfg_local):
    achados = achar_servidores(cfg_local)
    return achados[0] if achados else None


def _lancar(exe, args):
    """Inicia o llama-server na pasta dele (as DLLs/.so ficam ao lado) e sem janela."""
    pasta = str(Path(exe).parent)
    env = dict(os.environ)
    if sys.platform.startswith("linux"):
        env["LD_LIBRARY_PATH"] = pasta + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": pasta, "env": env}
    if sys.platform.startswith("win"):
        kw["creationflags"] = 0x08000000  # sem janela
    elif os.access(exe, os.X_OK) is False:
        try:
            os.chmod(exe, os.stat(exe).st_mode | 0o111)
        except OSError:
            pass
    return subprocess.Popen([exe] + args, **kw)


# ------------------------------------------------------------------ gerenciador
class LocalManager:
    """Liga e desliga o llama-server sozinho, com um modelo carregado por vez."""

    def __init__(self, settings, client):
        self.s = settings
        self.client = client
        self.proc = None
        self.atual = None
        self.estado = "parado"
        self.erro = None
        self.hw = None
        self._lock = asyncio.Lock()
        self._falhou_ate = 0.0

    async def ensure(self, cand):
        """Garante que o servidor está de pé com o modelo pedido. Levanta RuntimeError se não der."""
        async with self._lock:
            if time.time() < self._falhou_ate:
                raise RuntimeError(self.erro or "servidor local indisponível")
            if self.proc and self.proc.poll() is None and self.atual == cand["arquivo"]:
                return
            self._parar()
            cfg = self.s.cfg["local"]
            self.hw = self.hw or hardware()
            exes = achar_servidores(cfg, self.hw)
            if not exes:
                self._falha("llama-server não encontrado. Ele deveria vir dentro do Bruno IDE; se você montou o "
                            "editor sem o passo de embutir, rode: python app.py --instalar-llama "
                            "(ou indique o caminho em local.servidor no config.yaml)")
            if not Path(cand["arquivo"]).exists():
                self._falha(f"arquivo de modelo não encontrado: {cand['arquivo']}")
            plano = plano_de_carga(cand, self.hw)
            porta = int(cfg.get("porta", 8091))
            args = ["-m", cand["arquivo"], "--host", "127.0.0.1", "--port", str(porta),
                    "-c", str(plano["ctx"]), "-ngl", str(plano["ngl"]), "--jinja"]
            ultimo_erro = "o llama-server fechou ao iniciar (modelo grande demais para a memória?)"
            for exe in exes:  # se uma variante (ex.: CUDA sem driver) não abre, tenta a próxima
                variante = Path(exe).parent.name
                log(f"[local] iniciando {cand['modelo']} com {variante} "
                    f"(camadas na GPU: {plano['ngl']}, contexto: {plano['ctx']})")
                self.estado = "iniciando"
                try:
                    self.proc = _lancar(exe, args)
                except OSError as e:
                    ultimo_erro = f"não consegui iniciar o llama-server ({variante}): {e}"
                    log(f"[!] local: {ultimo_erro}")
                    continue
                limite = time.time() + float(cfg.get("espera_s", 180))
                while time.time() < limite:
                    if self.proc.poll() is not None:
                        ultimo_erro = (f"o llama-server ({variante}) fechou ao iniciar "
                                       "(modelo grande demais para a memória ou driver da placa incompatível?)")
                        log(f"[!] local: {ultimo_erro}")
                        break
                    try:
                        r = await self.client.get(f"http://127.0.0.1:{porta}/health", timeout=3)
                        if r.status_code == 200 and self.proc.poll() is None:
                            self.atual, self.estado, self.erro = cand["arquivo"], "pronto", None
                            log(f"[local] {cand['modelo']} pronto ({variante})")
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(1)
                else:
                    self._parar()
                    self._falha("o servidor local demorou demais para ficar pronto")
                self.proc = None
            self._falha(ultimo_erro)

    def _falha(self, msg):
        self.estado, self.erro = "erro", msg
        self._falhou_ate = time.time() + 60
        log(f"[!] local: {msg}")
        raise RuntimeError(msg)

    def _parar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.atual = None, None
        if self.estado != "erro":
            self.estado = "parado"

    def marcar_falha(self):
        """O servidor respondeu mal: força reinício na próxima chamada."""
        self._parar()

    def stop(self):
        self._parar()
