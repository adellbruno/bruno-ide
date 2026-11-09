# -*- mode: python -*-
# Gera o executável único do roteador:  pyinstaller roteador.spec
a = Analysis(["app.py"], pathex=["."],
             datas=[("config.yaml", "."), (".env.example", ".")],
             hiddenimports=["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                            "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on", "keyring.backends"],
             excludes=["tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="roteador", console=True, upx=False)
