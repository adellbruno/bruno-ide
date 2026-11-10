#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Roteador inteligente de IA para o Claude Code (v2).

O Claude Code fala com este programa como se ele fosse a API da Anthropic. Aqui dentro
decidimos qual IA responde (nuvem ou modelo local GGUF), alternamos chaves, respeitamos
limites e trocamos de provedor quando algo falha. Dados sensíveis ficam no modelo local.

Uso:  python app.py                  inicia o roteador
      python app.py --testar         testa cada modelo (texto, ferramenta e streaming)
      python app.py --modelos        confere se os modelos do config.yaml ainda existem
      python app.py --hardware       mede o computador e diz que modelo local cabe
      python app.py --chaves         assistente para guardar as chaves de API
      python app.py --token          mostra o token local e o endereço do painel
      python app.py --instalar-llama baixa o llama-server (modelos locais)
"""
import argparse
import asyncio
import hmac
import secrets
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from rot import dashboard
from rot.config import Settings, log
from rot.engine import Engine, err, estimate_tokens
from rot.state import State

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOSTS_OK = {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}


def garantir_token(dados):
    p = dados / "token.txt"
    if not p.exists():
        p.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        try:
            p.chmod(0o600)
        except OSError:
            pass
    return p.read_text(encoding="utf-8").strip()


def create_app(config_path=None, data_dir=None, env=None):
    settings = Settings(config_path=config_path, data_dir=data_dir, env=env)
    state = State(settings.data_dir)
    engine = Engine(settings, state)
    token = garantir_token(settings.data_dir) if settings.cfg["seguranca"].get("token_local", True) else None

    @asynccontextmanager
    async def lifespan(_app):
        if settings.cfg.get("verificar_modelos") != "desligado":
            asyncio.ensure_future(engine.check_models())
        yield
        await engine.close()

    app = FastAPI(title="Roteador de IA", lifespan=lifespan)
    app.state.engine, app.state.settings, app.state.token = engine, settings, token

    # ------------------------------------------------------------ segurança
    def host_ok(request):
        h = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") \
            else (request.headers.get("host") or "").split("]")[0] + "]"
        return h in HOSTS_OK

    def origem_ok(request):
        o = request.headers.get("origin")
        if not o:
            return True
        return o.split("://", 1)[-1].rsplit(":", 1)[0] in HOSTS_OK

    def tem_token_api(request):
        if not token:
            return True
        got = request.headers.get("x-api-key") or ""
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            got = got or auth[7:].strip()
        return bool(got) and hmac.compare_digest(got, token)

    def tem_token_painel(request):
        if not token:
            return True
        got = request.cookies.get("rt") or request.query_params.get("t") or ""
        return tem_token_api(request) or (bool(got) and hmac.compare_digest(got, token))

    @app.middleware("http")
    async def protecoes(request: Request, call_next):
        if not host_ok(request):
            return PlainTextResponse("host não permitido", status_code=403)
        if request.method == "POST" and request.url.path.startswith("/api/") and not origem_ok(request):
            return PlainTextResponse("origem não permitida", status_code=403)
        return await call_next(request)

    # --------------------------------------------------------------- API Anthropic
    @app.post("/v1/messages")
    async def messages(request: Request):
        if not tem_token_api(request):
            return err(401, "Token do roteador ausente ou incorreto.", "authentication_error")
        body = await request.json()
        return await engine.handle(body, request.headers)

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request):
        if not tem_token_api(request):
            return err(401, "Token do roteador ausente ou incorreto.", "authentication_error")
        return {"input_tokens": estimate_tokens(await request.json())}

    # ---------------------------------------------------------------------- painel
    @app.get("/")
    async def root():
        return {"roteador": "ativo", "painel": "/painel"}

    @app.get("/painel")
    async def painel(request: Request):
        t = request.query_params.get("t")
        if token and t and hmac.compare_digest(t, token):
            r = RedirectResponse("/painel", status_code=303)
            r.set_cookie("rt", token, httponly=True, samesite="strict", max_age=86400 * 30)
            return r
        if not tem_token_painel(request):
            return PlainTextResponse("Acesso negado. Abra o endereço completo mostrado no terminal do roteador "
                                     "(python app.py --token).", status_code=401)
        return HTMLResponse(dashboard.PAINEL_HTML)

    @app.get("/api/painel")
    async def api_painel(request: Request):
        if not tem_token_painel(request):
            return JSONResponse({"erro": "sem acesso"}, status_code=401)
        return engine.painel()

    @app.get("/api/historico.csv")
    async def api_csv(request: Request):
        if not tem_token_painel(request):
            return PlainTextResponse("sem acesso", status_code=401)
        return PlainTextResponse(dashboard.historico_csv(list(state.history)), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": "attachment; filename=historico.csv"})

    @app.get("/status")
    async def status(request: Request):
        if not tem_token_painel(request):
            return JSONResponse({"erro": "sem acesso"}, status_code=401)
        return engine.status()

    async def _acao(request, fn):
        if not tem_token_painel(request):
            return JSONResponse({"erro": "sem acesso"}, status_code=401)
        try:
            dados = await request.json()
        except ValueError:
            dados = {}
        return {"ok": bool(fn(dados))}

    @app.post("/api/pausar")
    async def api_pausar(request: Request):
        return await _acao(request, lambda d: engine.pausar(d.get("provedor", ""), bool(d.get("pausar"))))

    @app.post("/api/aprovar_ordem")
    async def api_ordem(request: Request):
        return await _acao(request, lambda d: engine.aprovar_ordem(d.get("nivel", "")))

    @app.post("/api/liberar_gasto")
    async def api_gasto(request: Request):
        return await _acao(request, lambda d: engine.liberar_gasto(d.get("provedor", "")))

    return app


# ---------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="Roteador de IA para o Claude Code")
    ap.add_argument("--testar", action="store_true")
    ap.add_argument("--modelos", action="store_true")
    ap.add_argument("--hardware", action="store_true")
    ap.add_argument("--chaves", action="store_true")
    ap.add_argument("--token", action="store_true")
    ap.add_argument("--instalar-llama", action="store_true")
    ap.add_argument("--verificar", action="store_true")
    ap.add_argument("--esperar", action="store_true")
    ap.add_argument("--url", action="store_true")
    ap.add_argument("--info-local", action="store_true")
    ap.add_argument("--local", choices=["on", "off"], default=None)
    ap.add_argument("--local-uso", choices=["reserva", "principal", "so_privado"], default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    from rot import diag

    settings = Settings(config_path=a.config)
    if a.token:
        t = garantir_token(settings.data_dir)
        print(t)
        return
    if a.url:
        print(f"http://127.0.0.1:{int(settings.cfg.get('porta', 8082))}")
        return
    if a.verificar:  # código de saída 0 se há ao menos uma chave (ou modelo local) configurada
        tem = any(p.keys for n, p in settings.providers.items() if not p.local) or settings.local_cands
        sys.exit(0 if tem else 1)
    if a.esperar:  # espera o roteador responder (até 30 s)
        import httpx
        url = f"http://127.0.0.1:{int(settings.cfg.get('porta', 8082))}/"
        for _ in range(60):
            try:
                if httpx.get(url, timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        sys.exit(1)
    if a.info_local:
        diag.info_local(settings)
        return
    if a.local:
        diag.definir_local(settings, a.local == "on")
        return
    if a.local_uso:
        diag.definir_local_uso(settings, a.local_uso)
        return
    if a.chaves:
        diag.assistente_chaves(settings)
        return
    if a.hardware:
        diag.mostrar_hardware(settings)
        return
    if a.instalar_llama:
        diag.instalar_llama(settings)
        return
    if a.testar or a.modelos:
        engine = Engine(settings, State(settings.data_dir))
        asyncio.run(diag.rodar(engine, testar=a.testar, modelos=a.modelos))
        return

    import uvicorn

    app = create_app(a.config)
    token = app.state.token
    porta = int(settings.cfg.get("porta", 8082))
    print("=" * 64)
    print(" Roteador de IA ativo")
    for n, p in settings.providers.items():
        print(f"   {n:<11} {'%d chave(s)' % len(p.keys) if p.keys else '- sem chave (ignorado)'}")
    print(f"\n Claude Code: aponte para http://127.0.0.1:{porta}")
    print(f" Painel:      http://127.0.0.1:{porta}/painel" + (f"?t={token}" if token else ""))
    print(" Marcas na mensagem: #rapido #medio #pro  #local #liberar  #economico #qualidade")
    print("=" * 64)
    if not any(p.keys for p in settings.providers.values()):
        print("ATENÇÃO: nenhuma chave configurada. Rode: python app.py --chaves")
    uvicorn.run(app, host="127.0.0.1", port=porta, log_level="warning")


if __name__ == "__main__":
    main()
