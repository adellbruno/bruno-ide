#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Suíte de testes do roteador. Um único comando:  python testes.py

Tudo roda localmente contra um provedor FALSO que imita os problemas reais (erro 429, 500,
resposta vazia, stream cortado, JSON de ferramenta quebrado...) e um llama-server falso.
Nenhuma chave real é usada. Opções:  python testes.py -k privacidade   (só testes com esse nome)
"""
import asyncio
import contextlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from fastapi.testclient import TestClient  # noqa: E402

from app import create_app  # noqa: E402
from rot import economy, privacy, tiering  # noqa: E402
from rot.config import deep_merge, DEFAULTS  # noqa: E402
from rot.local import gguf_info, plano_de_carga, recomendar  # noqa: E402
from rot.translate import repair_json  # noqa: E402

FAKE_PORT = 9099
FAKE = f"http://127.0.0.1:{FAKE_PORT}"
LOCAL_PORT = 9098
LOG = []
LOCK = threading.Lock()
DISPONIVEIS = ["m-texto", "m-bash", "m-ok", "m-vazio", "m-toolquebrado", "m-toolcortado", "m-cortado", "m-usage", "m-class", "m-opts400"]

# ============================================================ provedor falso
fake = FastAPI()


def chunk(delta, fin=None, usage=None):
    o = {"choices": [{"delta": delta, "finish_reason": fin}] if (delta or fin) else []}
    if usage:
        o["usage"] = usage
    return "data: " + json.dumps(o) + "\n\n"


def texto_do_pedido(b):
    out = []
    for m in b.get("messages", []):
        c = m.get("content")
        out.append(c if isinstance(c, str) else json.dumps(c))
    return "\n".join(out)


@fake.get("/{pfx}/models")
async def modelos(pfx: str):
    return {"data": [{"id": m} for m in DISPONIVEIS]}


@fake.post("/{pfx}/chat/completions")
async def chat(pfx: str, req: Request):
    b = await req.json()
    key = req.headers["authorization"].split()[-1]
    model = b["model"]
    stream = bool(b.get("stream"))
    texto = texto_do_pedido(b)
    with LOCK:
        LOG.append({"pfx": pfx, "model": model, "key": key, "tools": bool(b.get("tools")), "stream": stream,
                    "texto": texto, "opts": b.get("stream_options"), "msgs": b.get("messages")})
    if key == "ruim":
        return JSONResponse({"error": "limite"}, status_code=429, headers={"retry-after": "1"})
    if key == "k500":
        return JSONResponse({"error": "boom"}, status_code=500)
    if key == "k401":
        return JSONResponse({"error": "chave invalida"}, status_code=401)
    if model in ("m-404", "m-fantasma"):
        return JSONResponse({"error": "model not found"}, status_code=404)
    if model == "m-opts400" and b.get("stream_options"):
        return JSONResponse({"error": "unknown parameter: stream_options"}, status_code=400)
    if "Classifique a tarefa" in texto:
        saida = "complexo" if model == "m-class" else "simples"
        return {"choices": [{"message": {"role": "assistant", "content": saida}, "finish_reason": "stop"}]}

    quer_ferramenta = bool(b.get("tools")) and b["messages"][-1]["role"] != "tool"
    nome_ferr = "Ler"
    resposta = "eco:" + model
    if model == "m-texto":
        quer_ferramenta = False
    if model == "m-bash":   # imita um agente: primeiro pede o Bash, depois responde com o resultado
        nome_ferr = "Bash"
        ultimo = b["messages"][-1]
        quer_ferramenta = bool(b.get("tools")) and not any(m["role"] == "tool" for m in b["messages"])
        if not quer_ferramenta and ultimo["role"] == "tool":
            resposta = "feito: " + str(ultimo["content"])[:80]
    usage = {"prompt_tokens": 1000, "completion_tokens": 7} if model == "m-usage" else {"prompt_tokens": 5, "completion_tokens": 3}
    args = '{"arq":"a.py"}'
    if model == "m-bash":
        args = '{"command":"echo ola-do-roteador","description":"eco"}'
    if model == "m-toolquebrado":
        args = '{"arq":"a.py"'
    if model == "m-toolcortado":
        args = '{"arq":"a.p'
    if not stream:
        if model == "m-vazio":
            msg, fin = {"role": "assistant", "content": ""}, "stop"
        elif quer_ferramenta:
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": nome_ferr, "arguments": args}}]}
            fin = "length" if model == "m-toolcortado" else "tool_calls"
        else:
            msg, fin = {"role": "assistant", "content": resposta}, "stop"
        return {"choices": [{"message": msg, "finish_reason": fin}], "usage": usage}

    async def gen():
        if model == "m-vazio":
            yield chunk({}, "stop")
        elif model == "m-cortado":
            yield chunk({"content": "comecei "})
            await asyncio.sleep(0.05)
            raise RuntimeError("conexão derrubada")
        elif quer_ferramenta:
            yield chunk({"content": "vou ler "})
            yield chunk({"tool_calls": [{"index": 0, "id": "call_9", "type": "function",
                                         "function": {"name": nome_ferr, "arguments": ""}}]})
            meio = len(args) // 2
            yield chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[:meio]}}]})
            yield chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[meio:]}}]})
            yield chunk({}, "length" if model == "m-toolcortado" else "tool_calls")
        else:
            yield chunk({"content": resposta[:4]})
            yield chunk({"content": resposta[4:]})
            yield chunk({}, "stop")
        if b.get("stream_options"):
            yield chunk(None, None, usage)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def subir_fake():
    cfg = uvicorn.Config(fake, host="127.0.0.1", port=FAKE_PORT, log_level="critical")
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(f"{FAKE}/p1/models", timeout=1)
            return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError("provedor falso não subiu")


# ====================================================== llama-server falso
LLAMA_FALSO = r'''#!{py}
import json, sys, os
from http.server import BaseHTTPRequestHandler, HTTPServer
a = sys.argv
modelo = os.path.basename(a[a.index("-m") + 1]); porta = int(a[a.index("--port") + 1])
cont = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llama_log.txt")
open(cont, "a").write("start " + modelo + " " + " ".join(a[1:]) + "\n")
class H(BaseHTTPRequestHandler):
    def log_message(self, *x): pass
    def do_GET(self):
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def do_POST(self):
        n = int(self.headers.get("content-length", 0)); b = json.loads(self.rfile.read(n))
        open(cont, "a").write("req " + modelo + "\n")
        if b.get("stream"):
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            for d in ({"content": "local:"}, {"content": modelo}):
                self.wfile.write(("data: " + json.dumps({"choices": [{"delta": d, "finish_reason": None}]}) + "\n\n").encode())
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"role": "assistant", "content": "local:" + modelo}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}).encode())
HTTPServer(("127.0.0.1", porta), H).serve_forever()
'''


def escrever_gguf(caminho, camadas=32, ctx=4096, nome="modelo-falso"):
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    kv = []
    kv.append(s("general.architecture") + struct.pack("<I", 8) + s("llama"))
    kv.append(s("tokenizer.ggml.tokens") + struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", 3)
              + s("a") + s("bb") + s("ccc"))                      # array de textos: precisa ser pulado
    kv.append(s("tokenizer.ggml.scores") + struct.pack("<I", 9) + struct.pack("<I", 6) + struct.pack("<Q", 3)
              + struct.pack("<fff", 1, 2, 3))                      # array numérico
    kv.append(s("general.name") + struct.pack("<I", 8) + s(nome))
    kv.append(s("llama.block_count") + struct.pack("<I", 4) + struct.pack("<I", camadas))
    kv.append(s("llama.context_length") + struct.pack("<I", 4) + struct.pack("<I", ctx))
    dados = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, len(kv)) + b"".join(kv)
    Path(caminho).write_bytes(dados + b"\0" * 2048)


# ============================================================== utilidades
def C(modelo, prov="p1", **kw):
    d = {"provedor": prov, "modelo": modelo, "contexto": 128000, "max_tokens": 512}
    d.update(kw)
    return d


def cfg_base(niveis, extra=None, provedores=None):
    provs = provedores or {"p1": {"base_url": f"{FAKE}/p1", "variavel_env": "P1_KEYS"}}
    cfg = {"porta": 9097, "dados": "dados", "seguranca": {"token_local": False},
           "verificar_modelos": "desligado", "provedores": provs, "niveis": niveis}
    return deep_merge(cfg, extra or {})


@contextlib.contextmanager
def rodar(cfg, env=None, workdir=None):
    wd = Path(workdir or tempfile.mkdtemp(prefix="rot_teste_"))
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    app = create_app(str(wd / "config.yaml"), env=env if env is not None else {"P1_KEYS": "boa"})
    with TestClient(app) as c:
        with LOCK:
            LOG.clear()
        yield c, app.state.engine, wd
    if workdir is None:
        shutil.rmtree(wd, ignore_errors=True)


def pedido(texto="liste os arquivos", model="claude-sonnet-5-5", stream=False, tools=False, msgs=None, **kw):
    b = {"model": model, "max_tokens": 1000, "stream": stream,
         "system": [{"type": "text", "text": "Você é útil. Conta: dev@exemplo.com"}],
         "messages": msgs or [{"role": "user", "content": texto}]}
    if tools:
        b["tools"] = [{"name": "Ler", "description": "lê arquivo", "input_schema": {
            "type": "object", "$schema": "x", "additionalProperties": False,
            "properties": {"arq": {"type": "string"}}, "required": ["arq"]}}]
    b.update(kw)
    return b


def eventos(c, body):
    with c.stream("POST", "/v1/messages", json=body) as r:
        linhas = [ln for ln in r.iter_lines() if ln]
    return [json.loads(ln[5:]) for ln in linhas if ln.startswith("data:")]


def texto_stream(evs):
    return "".join(e["delta"]["text"] for e in evs if e["type"] == "content_block_delta" and e["delta"]["type"] == "text_delta")


def msg_usuario(texto):
    return {"role": "user", "content": texto}


def par_ferramenta(i, erro=False, conteudo="ok"):
    return [{"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "Ler", "input": {"arq": "a"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": conteudo,
                                          **({"is_error": True} if erro else {})}]}]


def com_prov(nome):
    with LOCK:
        return [x for x in LOG if x["pfx"] == nome]


def digitos_cpf(base9):
    d = [int(x) for x in base9]
    for n in (9, 10):
        d.append((sum(d[i] * (n + 1 - i) for i in range(n)) * 10) % 11 % 10)
    return "".join(map(str, d))


def cnpj_com_dv(base12):
    d = [int(x) for x in base12]
    for n, p in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        r = sum(d[i] * p[i] for i in range(n)) % 11
        d.append(0 if r < 2 else 11 - r)
    return "".join(map(str, d))


def cnj(n7="1234567", ano="2023", j="8", tr="19", orig="0001"):
    dd = 98 - int(n7 + ano + j + tr + orig + "00") % 97
    return f"{n7}-{dd:02d}.{ano}.{j}.{tr}.{orig}"


CPF = digitos_cpf("123456789")                    # 12345678909
CPF_F = f"{CPF[:3]}.{CPF[3:6]}.{CPF[6:9]}-{CPF[9:]}"
CNPJ = cnpj_com_dv("112223330001")
CNPJ_F = f"{CNPJ[:2]}.{CNPJ[2:5]}.{CNPJ[5:8]}/{CNPJ[8:12]}-{CNPJ[12:]}"
PROC = cnj()


def montar_local(wd, modelos=("modelo-a.gguf",), uso="reserva"):
    pasta = wd / "modelos"
    pasta.mkdir(exist_ok=True)
    for m in modelos:
        escrever_gguf(pasta / m, nome=m)
    exe = wd / "llama-server-falso"
    exe.write_text(LLAMA_FALSO.replace("{py}", sys.executable), encoding="utf-8")
    exe.chmod(0o755)
    return {"local": {"ativo": True, "uso": uso, "pasta_modelos": str(pasta), "servidor": str(exe),
                      "porta": LOCAL_PORT, "espera_s": 20,
                      "modelos": [{"arquivo": m, "nivel": "simples", "contexto": 8192} for m in modelos]}}


# ================================================================== TESTES
def teste_reparo_json():
    ok = {
        '{"a":1}': ({"a": 1}, False),
        '{"arq":"a.py"': ({"arq": "a.py"}, True),
        '{"a":1,}': ({"a": 1}, True),
        '```json\n{"a":1}\n```': ({"a": 1}, True),
        '{"a":1}}': ({"a": 1}, True),
        '{"a":[1,2': ({"a": [1, 2]}, True),
        "{'a': 'x'}": ({"a": "x"}, True),
        "": ({}, False),
    }
    for entrada, (esperado, consertado) in ok.items():
        obj, fix = repair_json(entrada)
        assert obj == esperado and fix == consertado, f"{entrada!r} -> {obj!r}, {fix}"
    for ruim in ['{"arq":"a.p', '{"a":1,"b":', '{"a":1,"b"', '{"cmd":"rm -rf /tmp/foo/ba']:
        obj, _ = repair_json(ruim)
        assert obj is None, f"não deveria consertar valor cortado: {ruim!r} -> {obj!r}"


def teste_traducao_ferramenta_sem_stream():
    with rodar(cfg_base({"simples": [C("m-ok")], "medio": [], "complexo": []})) as (c, e, wd):
        r = c.post("/v1/messages", json=pedido(tools=True)).json()
        tool = [b for b in r["content"] if b["type"] == "tool_use"]
        assert tool and tool[0]["input"] == {"arq": "a.py"}, r
        assert r["stop_reason"] == "tool_use", r
        r = c.post("/v1/messages", json=pedido()).json()
        assert r["content"][0]["text"] == "eco:m-ok", r


def teste_streaming_com_ferramenta():
    with rodar(cfg_base({"simples": [C("m-ok")]})) as (c, e, wd):
        evs = eventos(c, pedido(stream=True, tools=True))
        tipos = [x["type"] for x in evs]
        args = "".join(x["delta"]["partial_json"] for x in evs
                       if x["type"] == "content_block_delta" and x["delta"]["type"] == "input_json_delta")
        assert tipos[0] == "message_start" and tipos[-1] == "message_stop", tipos
        assert json.loads(args) == {"arq": "a.py"}, args
        assert [x for x in evs if x["type"] == "message_delta"][0]["delta"]["stop_reason"] == "tool_use"
        assert "vou ler" in json.dumps(evs)


def teste_rodizio_de_chaves_e_estado_persistido():
    cfg = cfg_base({"simples": [C("m-ok")]})
    wd = Path(tempfile.mkdtemp(prefix="rot_teste_"))
    with rodar(cfg, {"P1_KEYS": "ruim,boa"}, workdir=wd) as (c, e, _):
        r = c.post("/v1/messages", json=pedido()).json()
        assert r["content"][0]["text"] == "eco:m-ok", r
        assert [x["key"] for x in com_prov("p1")] == ["ruim", "boa"], com_prov("p1")
    # reinicia: a pausa da chave ruim deve ter sido lembrada
    with rodar(cfg, {"P1_KEYS": "ruim,boa"}, workdir=wd) as (c, e, _):
        c.post("/v1/messages", json=pedido())
        assert [x["key"] for x in com_prov("p1")] == ["boa"], com_prov("p1")
    shutil.rmtree(wd, ignore_errors=True)


def teste_ferramenta_consertada_no_stream():
    with rodar(cfg_base({"simples": [C("m-toolquebrado")]})) as (c, e, wd):
        evs = eventos(c, pedido(stream=True, tools=True))
        args = "".join(x["delta"]["partial_json"] for x in evs
                       if x["type"] == "content_block_delta" and x["delta"]["type"] == "input_json_delta")
        assert json.loads(args) == {"arq": "a.py"}, (args, evs)
        assert e.st.problems["p1/m-toolquebrado"].get("ferramenta_consertada") == 1


def teste_ferramenta_cortada_vai_para_o_proximo_modelo():
    with rodar(cfg_base({"simples": [C("m-toolcortado"), C("m-ok")]})) as (c, e, wd):
        r = c.post("/v1/messages", json=pedido(tools=True)).json()   # sem stream: troca na hora
        assert any(b["type"] == "tool_use" and b["input"] == {"arq": "a.py"} for b in r["content"]), r
        assert [x["model"] for x in com_prov("p1")] == ["m-toolcortado", "m-ok"]
    with rodar(cfg_base({"simples": [C("m-toolcortado"), C("m-ok")]})) as (c, e, wd):
        evs = eventos(c, pedido(stream=True, tools=True))  # stream: erro "overloaded" para o cliente tentar de novo
        assert evs[-1]["type"] == "error" and evs[-1]["error"]["type"] == "overloaded_error", evs[-1]
        evs2 = eventos(c, pedido(stream=True, tools=True))  # o modelo ruim está em pausa: agora vai ao bom
        assert evs2[-1]["type"] == "message_stop", evs2[-1]
        assert com_prov("p1")[-1]["model"] == "m-ok"


def teste_resposta_vazia_troca_de_modelo():
    with rodar(cfg_base({"simples": [C("m-vazio"), C("m-ok")]})) as (c, e, wd):
        r = c.post("/v1/messages", json=pedido()).json()
        assert r["content"][0]["text"] == "eco:m-ok", r
        evs = eventos(c, pedido("outra", stream=True))
        assert texto_stream(evs) == "eco:m-ok", evs
        assert e.st.problems["p1/m-vazio"]["resposta_vazia"] >= 1


def teste_stream_cortado_no_meio_nao_trava():
    with rodar(cfg_base({"simples": [C("m-cortado")]})) as (c, e, wd):
        evs = eventos(c, pedido(stream=True))
        assert texto_stream(evs).startswith("comecei"), evs
        assert evs[-1]["type"] == "error", evs[-1]


def teste_modelo_inexistente_e_verificacao_de_modelos():
    cfg = cfg_base({"simples": [C("m-404"), C("m-fantasma"), C("m-ok")]})
    with rodar(cfg) as (c, e, wd):
        r = c.post("/v1/messages", json=pedido()).json()
        assert r["content"][0]["text"] == "eco:m-ok", r
        c.portal.call(e.check_models)
        aus = e.ausentes()
        nomes = {a["modelo"] for a in aus}
        assert {"m-404", "m-fantasma"} <= nomes and "m-ok" not in nomes, aus
        assert any(a["modelo"] == "m-fantasma" for a in aus)
    cfg["verificar_modelos"] = "pular"
    with rodar(cfg) as (c, e, wd):
        c.portal.call(e.check_models)
        c.post("/v1/messages", json=pedido())
        assert [x["model"] for x in com_prov("p1")] == ["m-ok"], com_prov("p1")


def teste_stream_options_recusado_e_refeito():
    with rodar(cfg_base({"simples": [C("m-opts400")]})) as (c, e, wd):
        evs = eventos(c, pedido(stream=True))
        assert texto_stream(evs) == "eco:m-opts400", evs
        assert e.s.prov("p1").stream_usage is False


def teste_limite_por_minuto_passa_ao_proximo():
    provs = {"a": {"base_url": f"{FAKE}/pa", "variavel_env": "A_KEYS", "limite_por_minuto": 2},
             "b": {"base_url": f"{FAKE}/pb", "variavel_env": "B_KEYS"}}
    cfg = cfg_base({"simples": [C("m-ok", "a"), C("m-ok", "b")]}, provedores=provs)
    with rodar(cfg, {"A_KEYS": "x", "B_KEYS": "y"}) as (c, e, wd):
        for _ in range(3):
            c.post("/v1/messages", json=pedido())
        assert [x["pfx"] for x in LOG] == ["pa", "pa", "pb"], LOG


def teste_distribuicao_de_carga_por_cota():
    provs = {"a": {"base_url": f"{FAKE}/pa", "variavel_env": "A_KEYS", "limite_diario": 5},
             "b": {"base_url": f"{FAKE}/pb", "variavel_env": "B_KEYS"}}
    cfg = cfg_base({"simples": [C("m-ok", "a"), C("m-ok", "b")]}, provedores=provs)
    with rodar(cfg, {"A_KEYS": "x", "B_KEYS": "y"}) as (c, e, wd):
        for _ in range(5):
            c.post("/v1/messages", json=pedido())
        # depois de 4 de 5 usados, resta menos de 25% da cota de "a": o pedido seguinte vai para "b"
        assert [x["pfx"] for x in LOG] == ["pa", "pa", "pa", "pa", "pb"], [x["pfx"] for x in LOG]


def teste_tokens_reais_e_calibracao():
    with rodar(cfg_base({"simples": [C("m-usage")]})) as (c, e, wd):
        c.post("/v1/messages", json=pedido())
        d = e.st.tokens[[k for k in e.st.tokens][0]]
        assert d["in"] == 1000 and d["out"] == 7, d
        assert e.st.calib["p1/m-usage"] > 1.5, e.st.calib


def teste_escalada_e_descida_de_nivel():
    niveis = {"simples": [C("m-ok")], "medio": [C("m-usage")], "complexo": [C("m-class")]}
    with rodar(cfg_base(niveis)) as (c, e, wd):
        inicio = [msg_usuario("liste os arquivos")]
        c.post("/v1/messages", json=pedido(msgs=inicio))
        assert com_prov("p1")[-1]["model"] == "m-ok"
        m = inicio + par_ferramenta(1, erro=True) + par_ferramenta(2, erro=True)
        c.post("/v1/messages", json=pedido(msgs=m))
        assert com_prov("p1")[-1]["model"] == "m-usage", com_prov("p1")[-1]   # subiu para médio
        m2 = m + par_ferramenta(3, erro=True) + par_ferramenta(4, erro=True)
        c.post("/v1/messages", json=pedido(msgs=m2))
        assert com_prov("p1")[-1]["model"] == "m-class"                       # subiu para complexo
        m3 = m2 + sum((par_ferramenta(10 + i) for i in range(6)), [])
        c.post("/v1/messages", json=pedido(msgs=m3))
        assert com_prov("p1")[-1]["model"] == "m-ok", com_prov("p1")[-1]      # 6 sucessos: voltou
        assert e.st.learn["simples"]["p1/m-ok"]["escaladas"] >= 1, e.st.learn


def teste_atalhos_e_perfil_economico():
    niveis = {"simples": [C("m-ok", "pago"), C("m-usage", "livre")], "medio": [C("m-class", "livre")],
              "complexo": [C("m-opts400", "livre")]}
    provs = {"pago": {"base_url": f"{FAKE}/pg", "variavel_env": "PG", "pago": True},
             "livre": {"base_url": f"{FAKE}/lv", "variavel_env": "LV"}}
    with rodar(cfg_base(niveis, provedores=provs), {"PG": "a", "LV": "b"}) as (c, e, wd):
        c.post("/v1/messages", json=pedido("liste isso"))
        assert com_prov("pg"), "sem perfil, o pago (primeiro da lista) deveria atender"
        with LOCK:
            LOG.clear()
        c.post("/v1/messages", json=pedido("liste isso #economico"))
        assert com_prov("lv") and not com_prov("pg"), LOG
        with LOCK:
            LOG.clear()
        r = c.post("/v1/messages", json=pedido("#pro faça isso")).json()
        assert com_prov("lv")[-1]["model"] == "m-opts400", LOG


def teste_classificador_opcional_e_nao_vaza_dado_sensivel():
    niveis = {"simples": [C("m-class")], "medio": [C("m-usage")], "complexo": [C("m-opts400")]}
    texto = "Preciso corrigir o cálculo de juros no módulo de contratos, que está errado em alguns casos."
    cfg = cfg_base(niveis, {"roteamento": {"classificador_llm": {"ativo": True, "timeout_s": 5},
                                          "palavras_complexas": ["corrigir"]}})
    with rodar(cfg) as (c, e, wd):
        c.post("/v1/messages", json=pedido(texto))
        assert any("Classifique" in x["texto"] for x in LOG), "o classificador deveria ter sido chamado"
        assert LOG[-1]["model"] == "m-opts400", LOG[-1]   # classificou como complexo
    with rodar(cfg) as (c, e, wd):  # com dado sensível o texto não pode ir ao classificador
        c.post("/v1/messages", json=pedido(texto + f" Processo {PROC}.", ),)
        assert not any("Classifique" in x["texto"] for x in LOG), LOG


def teste_detector_de_dados():
    ok = privacy.scan_text
    todas = privacy.TODAS
    assert "processo" in ok(f"autos {PROC}", todas)
    assert "processo" in ok("proc " + PROC.replace("-", "").replace(".", ""), todas)   # sem pontuação, DV válido
    assert "cpf" in ok(f"CPF {CPF_F}", todas) and "cpf" in ok(f"cpf {CPF}", todas)
    assert "cnpj" in ok(f"CNPJ {CNPJ_F}", todas) and "cnpj" in ok(f"x {CNPJ} y", todas)
    assert "conta" in ok("Agência 1234 conta corrente 56789-0", todas)
    assert "telefone" in ok("ligue (21) 99999-8888", todas)
    assert "email" in ok("fulano@exemplo.com.br", todas)
    # falsos positivos que NÃO podem acender
    assert not ok("12345678901", todas), "11 dígitos com DV inválido não é CPF"
    assert not ok("timestamp 1699999999999 e 20230101123456789012", todas)
    assert not ok("versão 3.12.4 e porta 8082, linha 1234-5678?", ["cpf", "processo", "cnpj"])
    assert not ok("fulano@exemplo.com", ["processo", "cpf", "cnpj", "conta", "telefone"])  # e-mail desligado por padrão
    assert cnj_ok(PROC)


def cnj_ok(p):
    return privacy.cnj_valido(p)


def _cfg_priv(wd, extra=None, uso="reserva"):
    return cfg_base({"simples": [C("m-ok")], "medio": [], "complexo": []},
                    deep_merge(montar_local(wd, uso=uso), extra or {}))


def teste_privacidade_so_vai_para_o_local():
    wd = Path(tempfile.mkdtemp(prefix="rot_teste_"))
    cfg = _cfg_priv(wd)
    with rodar(cfg, workdir=wd) as (c, e, _):
        casos = [
            pedido(f"analise o processo {PROC}"),
            pedido(f"cpf do executado: {CPF}"),
            pedido(msgs=[msg_usuario("leia a minuta")] + par_ferramenta(1, conteudo=f"Executada: empresa CNPJ {CNPJ_F}")),
            pedido(msgs=[msg_usuario("abra o arquivo")] + [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "z", "name": "Ler", "input": {"arq": "x"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "z",
                                              "content": [{"type": "text", "text": f"Peça inicial, autos {PROC}"}]}]}]),
            pedido("revise", stream=True, msgs=[msg_usuario(f"Agência 1234 conta corrente 56789-0 do réu")]),
        ]
        for i, b in enumerate(casos):
            if b.get("stream"):
                evs = eventos(c, b)
                assert texto_stream(evs).startswith("local:"), (i, evs)
            else:
                r = c.post("/v1/messages", json=b).json()
                assert r["content"][0]["text"].startswith("local:"), (i, r)
        assert not LOG, f"NADA deveria ter ido ao provedor externo: {LOG}"
        # pedido comum continua indo para a nuvem
        r = c.post("/v1/messages", json=pedido("liste os arquivos do projeto")).json()
        assert r["content"][0]["text"] == "eco:m-ok", r
        # lembrete do sistema e prompt de sistema com e-mail/CPF NÃO contam
        with LOCK:
            LOG.clear()
        b = pedido("oi", msgs=[{"role": "user", "content": [
            {"type": "text", "text": f"<system-reminder>cpf {CPF_F} email a@b.com</system-reminder>"},
            {"type": "text", "text": "faça uma lista simples"}]}])
        c.post("/v1/messages", json=b)
        assert LOG, "dados dentro de <system-reminder> não devem bloquear"
    shutil.rmtree(wd, ignore_errors=True)


def teste_privacidade_persiste_na_conversa_e_liberar():
    wd = Path(tempfile.mkdtemp(prefix="rot_teste_"))
    with rodar(_cfg_priv(wd), workdir=wd) as (c, e, _):
        base = [msg_usuario(f"veja o processo {PROC}")]
        c.post("/v1/messages", json=pedido(msgs=base))
        # continuação do agente: o histórico ainda carrega o dado, e a conversa segue privada
        c.post("/v1/messages", json=pedido(msgs=base + par_ferramenta(1)))
        assert not LOG, LOG
        # #liberar numa mensagem nova da mesma conversa permite enviar à nuvem
        c.post("/v1/messages", json=pedido(msgs=base + par_ferramenta(1) + [msg_usuario("ok, pode usar a nuvem #liberar")]))
        assert LOG and LOG[-1]["model"] == "m-ok"
        ult = list(e.st.history)[-1]
        assert ult["externo_sensivel"] is True, ult
        assert any(a["nivel"] == "critico" for a in e.alertas())
    shutil.rmtree(wd, ignore_errors=True)


def teste_privacidade_sem_modelo_local():
    cfg = cfg_base({"simples": [C("m-ok")]})
    with rodar(cfg) as (c, e, wd):
        r = c.post("/v1/messages", json=pedido(f"processo {PROC}"))
        assert r.status_code == 403 and "Nada foi enviado" in r.json()["error"]["message"], r.text
        assert not LOG, LOG
        assert e.painel()["privacidade"]["barrados_hoje"] == 1
        r = c.post("/v1/messages", json=pedido(f"processo {PROC} #liberar"))
        assert r.status_code == 200 and LOG
    cfg2 = cfg_base({"simples": [C("m-ok")]}, {"privacidade": {"sem_local": "avisar"}})
    with rodar(cfg2) as (c, e, wd):
        r = c.post("/v1/messages", json=pedido(f"processo {PROC}"))
        assert r.status_code == 200 and LOG
        assert any(ev["tipo"] == "privacidade" for ev in e.st.events)
    cfg3 = cfg_base({"simples": [C("m-ok")]}, {"privacidade": {"ativo": False}})
    with rodar(cfg3) as (c, e, wd):
        assert c.post("/v1/messages", json=pedido(f"processo {PROC}")).status_code == 200


def teste_pasta_protegida_e_marca_local():
    wd = Path(tempfile.mkdtemp(prefix="rot_teste_"))
    cfg = _cfg_priv(wd, {"privacidade": {"pastas_protegidas": ["crm_juridico", "C:/Users/Bruno/Processos"]}})
    with rodar(cfg, workdir=wd) as (c, e, _):
        m = [msg_usuario("leia o arquivo")] + [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "q", "name": "Ler",
                                              "input": {"arq": "C:\\Users\\Bruno\\Processos\\caso1.txt"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "q", "content": "texto"}]}]
        r = c.post("/v1/messages", json=pedido(msgs=m)).json()
        assert r["content"][0]["text"].startswith("local:"), r
        r = c.post("/v1/messages", json=pedido("explique este projeto #local")).json()
        assert r["content"][0]["text"].startswith("local:"), r
        assert not LOG, LOG
    shutil.rmtree(wd, ignore_errors=True)


def teste_historico_e_estado_nao_guardam_dados():
    wd = Path(tempfile.mkdtemp(prefix="rot_teste_"))
    segredo = "chave-secreta-abcdef123456"
    with rodar(_cfg_priv(wd), {"P1_KEYS": segredo}, workdir=wd) as (c, e, _):
        c.post("/v1/messages", json=pedido(f"processo {PROC} cpf {CPF_F}"))
        c.post("/v1/messages", json=pedido("liste os arquivos do projeto"))
        c.get("/api/painel")
    tudo = ""
    for p in (wd / "dados").rglob("*"):
        if p.is_file() and "backups" not in p.parts:
            tudo += p.read_text(encoding="utf-8", errors="ignore")
    for proibido in (PROC, CPF, CPF_F, segredo, "liste os arquivos"):
        assert proibido not in tudo, f"vazou nos arquivos de dados: {proibido}"
    shutil.rmtree(wd, ignore_errors=True)


def teste_gguf_hardware_e_servidor_local():
    wd = Path(tempfile.mkdtemp(prefix="rot_teste_"))
    escrever_gguf(wd / "a.gguf", camadas=28, ctx=32768)
    info = gguf_info(wd / "a.gguf")
    assert info["camadas"] == 28 and info["contexto_max"] == 32768 and info["arquitetura"] == "llama", info
    (wd / "lixo.gguf").write_bytes(b"nao e gguf")
    assert gguf_info(wd / "lixo.gguf") == {}
    hw = {"ram_gb": 16, "vram_gb": 8.0, "gpu": "X"}
    p = plano_de_carga({"arquivo": str(wd / "a.gguf"), "contexto": 100000, "gpu_camadas": "auto"}, hw)
    assert p["ctx"] == 32768 and p["ngl"] == 999, p                                  # cabe inteiro na GPU
    p = plano_de_carga({"arquivo": str(wd / "a.gguf"), "contexto": 4096, "gpu_camadas": "auto"}, {"ram_gb": 8, "vram_gb": 0})
    assert p["ngl"] == 0 and p["ctx"] == 4096, p
    assert "bilhões" in recomendar(hw)["texto"] and recomendar({"ram_gb": None, "vram_gb": 0})["bilhoes"] is None
    # servidor local: sobe sozinho, troca de modelo, e fecha junto com o roteador
    cfg = _cfg_priv(wd, uso="so_privado") if False else cfg_base({"simples": [C("m-ok")]}, montar_local(wd, ("a.gguf", "b.gguf")))
    cfg["local"]["modelos"][1]["nivel"] = "medio"
    with rodar(cfg, workdir=wd) as (c, e, _):
        r = c.post("/v1/messages", json=pedido("#local liste")).json()
        assert r["content"][0]["text"] == "local:a.gguf", r
        proc1 = e.local.proc
        assert proc1 and proc1.poll() is None
        r = c.post("/v1/messages", json=pedido("#local #medio liste")).json()
        assert r["content"][0]["text"] == "local:b.gguf", r        # trocou para o modelo do nível médio
        assert proc1.poll() is not None, "o servidor antigo deveria ter sido encerrado"
        proc2 = e.local.proc
        log = (wd / "llama_log.txt").read_text()
        assert "-ngl 0" in log and "--jinja" in log, log
    assert proc2.poll() is not None, "o llama-server deveria fechar junto com o roteador"
    shutil.rmtree(wd, ignore_errors=True)


def teste_local_como_reserva_quando_a_nuvem_falha():
    wd = Path(tempfile.mkdtemp(prefix="rot_teste_"))
    cfg = cfg_base({"simples": [C("m-404")]}, montar_local(wd, uso="reserva"))
    with rodar(cfg, workdir=wd) as (c, e, _):
        r = c.post("/v1/messages", json=pedido("liste os arquivos")).json()
        assert r["content"][0]["text"] == "local:modelo-a.gguf", r
    cfg = cfg_base({"simples": [C("m-ok")]}, montar_local(wd, uso="so_privado"))
    with rodar(cfg, workdir=wd) as (c, e, _):
        r = c.post("/v1/messages", json=pedido("liste os arquivos")).json()
        assert r["content"][0]["text"] == "eco:m-ok", r              # so_privado: não usa o local em pedido comum
    shutil.rmtree(wd, ignore_errors=True)


def teste_servidor_local_ausente_vira_erro_claro():
    wd = Path(tempfile.mkdtemp(prefix="rot_teste_"))
    ex = montar_local(wd)
    ex["local"]["servidor"] = str(wd / "nao-existe")
    cfg = cfg_base({"simples": [C("m-ok")]}, ex)
    with rodar(cfg, workdir=wd) as (c, e, _):
        r = c.post("/v1/messages", json=pedido(f"processo {PROC}"))
        assert r.status_code == 429, r.text            # todos ocupados, e NADA foi para a nuvem
        assert not LOG
        assert "llama-server não encontrado" in (e.local.erro or ""), e.local.erro
    shutil.rmtree(wd, ignore_errors=True)


def teste_economia_de_tokens():
    grande = "x" * 50000
    msgs = [msg_usuario("início")] + par_ferramenta(1, conteudo=grande) + par_ferramenta(2, conteudo=grande)
    b = pedido(msgs=msgs)
    b2 = economy.truncar_resultados(b, 1000, 3000)
    r1 = b2["messages"][2]["content"][0]["content"]
    r2 = b2["messages"][4]["content"][0]["content"]
    assert len(r1) < 1500 and "omitidos" in r1 and len(r2) < 3500 and len(r2) > len(r1), (len(r1), len(r2))
    assert b["messages"][2]["content"][0]["content"] == grande, "não pode alterar o pedido original"
    # ferramentas enxutas: mantém as já usadas e as que casam com o pedido
    ferr = [{"name": f"T{i}", "description": "faz coisas", "input_schema": {}} for i in range(30)]
    ferr.append({"name": "Gravar", "description": "grava arquivo em disco", "input_schema": {}})
    bb = {"tools": ferr, "messages": [msg_usuario("preciso gravar um arquivo")] + par_ferramenta(1)}
    bb["messages"][1]["content"][0]["name"] = "T7"
    out = economy.enxugar_ferramentas(bb, 10)
    nomes = [t["name"] for t in out["tools"]]
    assert len(nomes) == 10 and "T7" in nomes and "Gravar" in nomes, nomes
    # compactação: mantém os pares ferramenta/resultado válidos
    longa = [msg_usuario("primeiro pedido")]
    for i in range(12):
        longa += par_ferramenta(i)
        if i % 4 == 3:
            longa.append({"role": "assistant", "content": "feito"})
            longa.append(msg_usuario(f"pedido {i}"))
    longa += par_ferramenta(99)
    comp = economy.compactar({"messages": longa}, 6)["messages"]
    assert len(comp) < len(longa) and "Resumo automático" in json.dumps(comp[0], ensure_ascii=False)
    abertos = set()
    for m in comp:
        if isinstance(m["content"], list):
            for blk in m["content"]:
                if blk.get("type") == "tool_use":
                    abertos.add(blk["id"])
                if blk.get("type") == "tool_result":
                    assert blk["tool_use_id"] in abertos, "resultado órfão depois da compactação"
    assert comp[0]["role"] == "user"
    # cache: o segundo pedido idêntico não chega ao provedor
    cfg = cfg_base({"simples": [C("m-ok")]}, {"economia": {"cache_respostas": {"ativo": True, "segundos": 60}}})
    with rodar(cfg) as (c, e, wd):
        c.post("/v1/messages", json=pedido("liste"))
        c.post("/v1/messages", json=pedido("liste"))
        assert len(LOG) == 1, LOG
        c.post("/v1/messages", json=pedido("liste #nocache"))
        assert len(LOG) == 2


def teste_economia_ligada_no_roteador_e_medicao():
    cfg = cfg_base({"simples": [C("m-ok")]}, {"economia": {"modo": "leve"}})
    msgs = [msg_usuario("leia")] + par_ferramenta(1, conteudo="y" * 40000) + par_ferramenta(2, conteudo="curto")
    with rodar(cfg) as (c, e, wd):
        c.post("/v1/messages", json=pedido(msgs=msgs, tools=True))
        assert "omitidos" in LOG[-1]["texto"], "o resultado antigo deveria ter sido cortado"
        h = list(e.st.history)[-1]
        assert set(h["pesos"]) == {"sistema", "ferramentas", "historico", "ultima"}, h


def teste_seguranca_token_e_host():
    cfg = cfg_base({"simples": [C("m-ok")]}, {"seguranca": {"token_local": True}})
    with rodar(cfg) as (c, e, wd):
        token = (wd / "dados" / "token.txt").read_text().strip()
        assert len(token) >= 32
        assert c.post("/v1/messages", json=pedido()).status_code == 401
        assert c.post("/v1/messages", json=pedido(), headers={"authorization": "Bearer errado"}).status_code == 401
        assert c.post("/v1/messages", json=pedido(), headers={"authorization": f"Bearer {token}"}).status_code == 200
        assert c.post("/v1/messages", json=pedido(), headers={"x-api-key": token}).status_code == 200
        assert c.get("/painel").status_code == 401 and c.get("/api/painel").status_code == 401
        r = c.get(f"/painel?t={token}", follow_redirects=False)
        assert r.status_code == 303 and "rt=" in r.headers.get("set-cookie", ""), r.headers
        assert c.get("/painel").status_code == 200          # cookie guardado pelo cliente
        assert c.get("/api/painel").status_code == 200
        assert c.get("/", headers={"host": "evil.com"}).status_code == 403
        r = c.post("/api/pausar", json={"provedor": "p1", "pausar": True}, headers={"origin": "http://evil.com"})
        assert r.status_code == 403, r.status_code
        assert "p1" not in e.st.paused


def teste_painel_pausa_csv_e_alertas():
    provs = {"a": {"base_url": f"{FAKE}/pa", "variavel_env": "A", "limite_diario": 3},
             "b": {"base_url": f"{FAKE}/pb", "variavel_env": "B"}}
    cfg = cfg_base({"simples": [C("m-ok", "a"), C("m-ok", "b")]}, provedores=provs)
    with rodar(cfg, {"A": "k1,k2", "B": "z"}) as (c, e, wd):
        c.post("/v1/messages", json=pedido())
        d = c.get("/api/painel").json()
        assert d["pedidos_hoje"] == 1 and d["provedores"][0]["chaves"][0]["usado"] == 1, d
        assert c.post("/api/pausar", json={"provedor": "a", "pausar": True}).json()["ok"]
        with LOCK:
            LOG.clear()
        c.post("/v1/messages", json=pedido())
        assert [x["pfx"] for x in LOG] == ["pb"], LOG
        assert c.post("/api/pausar", json={"provedor": "a", "pausar": False}).json()["ok"]
        csv_txt = c.get("/api/historico.csv").text
        assert csv_txt.startswith("hora,nivel") and "p1" not in csv_txt and "liste" not in csv_txt
        e.st.daily[f"a|{e.s.prov('a').kids[0]}|{time.strftime('%Y-%m-%d')}"] = 3
        e.st.daily[f"a|{e.s.prov('a').kids[1]}|{time.strftime('%Y-%m-%d')}"] = 3
        msgs = [a["msg"] for a in c.get("/api/painel").json()["alertas"]]
        assert any("cota diária" in m for m in msgs), msgs


def teste_limite_de_gasto_e_liberacao():
    provs = {"p": {"base_url": f"{FAKE}/pp", "variavel_env": "P", "pago": True, "limite_gasto_dia_usd": 0.01}}
    cfg = cfg_base({"simples": [C("m-usage", "p", preco_milhao=[10, 20])]}, provedores=provs)
    with rodar(cfg, {"P": "k"}) as (c, e, wd):
        assert c.post("/v1/messages", json=pedido()).status_code == 200     # custo ~US$ 0,010 (1000 tokens x 10/milhão)
        assert e.st.spent("p") > 0
        r = c.post("/v1/messages", json=pedido())
        assert r.status_code == 503, r.status_code                          # limite atingido: bloqueado
        assert any("limite de gasto" in a["msg"] for a in e.alertas())
        assert c.post("/api/liberar_gasto", json={"provedor": "p"}).json()["ok"]
        assert c.post("/v1/messages", json=pedido()).status_code == 200


def teste_recarga_do_config_e_backup():
    cfg = cfg_base({"simples": [C("m-ok")]})
    with rodar(cfg) as (c, e, wd):
        c.post("/v1/messages", json=pedido())
        cfg2 = cfg_base({"simples": [C("m-usage")]})
        time.sleep(0.05)
        (wd / "config.yaml").write_text(yaml.safe_dump(cfg2), encoding="utf-8")
        os.utime(wd / "config.yaml", (time.time() + 5, time.time() + 5))
        e.s._checked = 0
        c.post("/v1/messages", json=pedido())
        assert LOG[-1]["model"] == "m-usage", LOG[-1]
        assert len(list((wd / "dados" / "backups").glob("config-*.yaml"))) == 2
        (wd / "config.yaml").write_text("niveis: [isto: não é: yaml válido", encoding="utf-8")
        os.utime(wd / "config.yaml", (time.time() + 10, time.time() + 10))
        e.s._checked = 0
        assert c.post("/v1/messages", json=pedido()).status_code == 200      # manteve o config anterior
        assert LOG[-1]["model"] == "m-usage"


def teste_aprendizado_sugere_e_aplica_nova_ordem():
    cfg = cfg_base({"simples": [C("m-ok"), C("m-usage")]})
    with rodar(cfg) as (c, e, wd):
        e.st.learn = {"simples": {"p1/m-ok": {"n": 40, "falhas": 20, "escaladas": 0},
                                  "p1/m-usage": {"n": 40, "falhas": 2, "escaladas": 0}}}
        s = e.sugestoes()
        assert s and s[0]["mover"] == "p1/m-usage" and s[0]["nova_ordem"][0] == "p1/m-usage", s
        assert c.post("/api/aprovar_ordem", json={"nivel": "simples"}).json()["ok"]
        c.post("/v1/messages", json=pedido("liste"))
        assert LOG[-1]["model"] == "m-usage", LOG[-1]


def teste_modelo_sem_ferramentas_nao_recebe_agente():
    cfg = cfg_base({"simples": [C("m-usage", tools=False), C("m-ok")]})
    with rodar(cfg) as (c, e, wd):
        c.post("/v1/messages", json=pedido(tools=True))
        assert LOG[-1]["model"] == "m-ok", LOG
        c.post("/v1/messages", json=pedido("liste"))
        assert LOG[-1]["model"] == "m-usage", LOG


def teste_chave_recusada_e_erro_5xx():
    cfg = cfg_base({"simples": [C("m-ok")]})
    with rodar(cfg, {"P1_KEYS": "k401,boa"}) as (c, e, wd):
        r = c.post("/v1/messages", json=pedido()).json()
        assert r["content"][0]["text"] == "eco:m-ok", r
        assert any(a["msg"].startswith("p1: 1 chave") for a in e.alertas()), e.alertas()
        assert any(ev["tipo"] == "chave_desativada" for ev in e.st.events)
    with rodar(cfg_base({"simples": [C("m-404"), C("m-ok")]}), {"P1_KEYS": "k500"}) as (c, e, wd):
        r = c.post("/v1/messages", json=pedido())      # 5xx no primeiro modelo, 5xx no segundo: tenta os dois
        assert r.status_code == 429 and [x["model"] for x in LOG] == ["m-404", "m-ok"], (r.status_code, LOG)


def teste_todos_no_limite_devolve_429_com_espera():
    cfg = cfg_base({"simples": [C("m-ok")]})
    with rodar(cfg, {"P1_KEYS": "ruim"}) as (c, e, wd):
        r = c.post("/v1/messages", json=pedido())
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1, (r.status_code, r.headers)
        assert r.json()["error"]["type"] == "rate_limit_error"


def teste_carga_dez_pedidos_simultaneos():
    wd = Path(tempfile.mkdtemp(prefix="rot_teste_"))
    cfg = cfg_base({"simples": [C("m-ok")]})
    (wd / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    app = create_app(str(wd / "config.yaml"), env={"P1_KEYS": "ruim,k1,k2"})
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=9097, log_level="critical"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get("http://127.0.0.1:9097/", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    with LOCK:
        LOG.clear()

    async def um(cli, i, stream):
        r = await cli.post("http://127.0.0.1:9097/v1/messages", json=pedido(f"tarefa {i}", stream=stream, tools=False))
        return r.status_code, r.text

    async def todos():
        async with httpx.AsyncClient(timeout=30) as cli:
            return await asyncio.gather(*[um(cli, i, i % 2 == 0) for i in range(10)])
    res = asyncio.run(todos())
    srv.should_exit = True
    assert all(s == 200 for s, _ in res), [s for s, _ in res]
    assert all("m-ok" in t for _, t in res), res[0]
    usadas = {x["key"] for x in LOG}
    assert {"k1", "k2"} <= usadas, usadas                     # as duas chaves boas foram usadas
    shutil.rmtree(wd, ignore_errors=True)


def teste_avaliador_de_tarefas():
    import avaliar
    assert avaliar.extrair_codigo("Aqui:\n```python\ndef f(x):\n    return x+1\n```\nfim") == "def f(x):\n    return x+1"
    t = avaliar.TAREFAS[0]
    assert avaliar.corrigir(t, t["_gabarito"]) is True
    assert avaliar.corrigir(t, "def nada():\n    pass") is False
    assert avaliar.corrigir(t, "while True:\n    pass", limite=2) is False   # loop infinito não trava
    assert len(avaliar.TAREFAS) >= 12
    for t in avaliar.TAREFAS:
        assert avaliar.corrigir(t, t["_gabarito"]), f"gabarito da tarefa {t['id']} não passa nos próprios testes"


# ============================================================ execução
def main():
    filtro = None
    if "-k" in sys.argv:
        filtro = sys.argv[sys.argv.index("-k") + 1]
    subir_fake()
    testes = [(n, f) for n, f in sorted(globals().items(), key=lambda kv: kv[1].__code__.co_firstlineno
                                       if callable(kv[1]) and hasattr(kv[1], "__code__") else 0)
              if n.startswith("teste_") and (not filtro or filtro in n)]
    falhas = []
    t0 = time.time()
    for nome, fn in testes:
        ini = time.time()
        try:
            fn()
            print(f"  OK     {nome[6:]}  ({time.time() - ini:.1f}s)")
        except Exception:
            falhas.append(nome)
            print(f"  FALHA  {nome[6:]}")
            print("         " + traceback.format_exc().strip().replace("\n", "\n         "))
    print(f"\n{len(testes) - len(falhas)}/{len(testes)} testes passaram em {time.time() - t0:.0f}s")
    print("TUDO CERTO" if not falhas else "HÁ FALHAS: " + ", ".join(falhas))
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
