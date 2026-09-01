"""Deterministic market-risk engine (SPEC sections 1-5).

Pure deterministic Python: no AI, no network, no .env. All randomness comes
from a single seeded numpy Generator.
"""

__all__ = ["curves", "esg", "pricing", "var", "run", "attribution"]
