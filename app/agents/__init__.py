"""Agent layer (SPEC-APP sections 2, 4, 5).

Packages:
  tools.py     tool registry — the agents' only reach into the world
  citation.py  claim extraction + binding; the auditability property
  runtime.py   mock/live dispatch; the ONLY file that reads .env
  style.py      templated prose wrapping deterministic check results
  personas/    the 11 built-in personas
  checks/      the deterministic mock checks behind each persona
  api.py       the shared interface called by app.server

Import discipline: this package imports app.server.db (the shared SQLite
layer) and app.config (shared limit configuration — a leaf module with no
server dependency), plus stdlib / engine-adjacent file reads. It never
imports app.server.main, app.server.engine_bridge or app.server.events.
"""
