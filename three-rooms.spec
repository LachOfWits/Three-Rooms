# PyInstaller spec — builds three-rooms.exe (PENDING-JUDGE §5).
#
#   .venv/Scripts/python -m PyInstaller --clean three-rooms.spec
#
# The exe carries the app, the engine, the frontend, the committed model
# inputs and the saved cycles, so a judge needs nothing but the file.
# Writable state (database, any runs they trigger) is created beside the exe
# at launch, not inside the bundle.

from pathlib import Path

ROOT = Path.cwd()

datas = [
    (str(ROOT / "app" / "static"), "app/static"),
    (str(ROOT / "assumptions"), "assumptions"),
    (str(ROOT / "book"), "book"),
    (str(ROOT / "data" / "processed"), "data/processed"),
    (str(ROOT / "outputs"), "outputs"),
    (str(ROOT / "scenarios"), "scenarios"),
    (str(ROOT / "AGENT-PROMPTS.md"), "."),
    (str(ROOT / "SPEC.md"), "."),
    (str(ROOT / "SPEC-APP.md"), "."),
]
if (ROOT / "saved_cycles").exists():
    datas.append((str(ROOT / "saved_cycles"), "saved_cycles"))

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=[
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
        "anthropic", "dotenv", "yaml", "numpy", "pandas",
        "app.server.main", "app.server.cycles", "app.server.session",
        "app.agents.api", "app.agents.runtime", "app.agents.research",
        "engine.run", "engine.attribution",
    ],
    excludes=["matplotlib", "tkinter", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="three-rooms",
    console=True,          # a judge should see "starting…" and any error
    disable_windowed_traceback=False,
    upx=False,
)
