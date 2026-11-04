"""AMCA: Adaptive Multi-Model Collaborative Cognition Architecture.

The package deliberately exposes a small public surface while the command-line
kernel is being validated. Provider SDKs are optional and imported lazily, so
``import amca`` remains local and side-effect free.
"""

from __future__ import annotations

__all__ = [
    "__version__",
    "USER_AGENT",
    "PROVIDER_REQUEST_TIMEOUT_SECONDS",
]

__version__ = "1.1.21"

#: The ceiling one provider request may take.
#:
#: The health probe and the runtime share this number on purpose: a probe
#: that is stricter than the runtime measures something the runtime never
#: asks for.  A model that answers in 40s was therefore "Provider down"
#: (blocked, unusable) while the same model would have run fine.  Sharing the
#: constant makes that class of false verdict impossible to reintroduce.
PROVIDER_REQUEST_TIMEOUT_SECONDS = 120.0

#: The User-Agent AMCA presents to providers.
#:
#: Python's default (``Python-urllib/3.x``) is on a number of gateway
#: blocklists: a Cloudflare-fronted OpenAI-compatible endpoint answers it
#: with HTTP 403 Error 1010 ("browser signature banned"), which is NOT an
#: authentication failure — the very same request with this User-Agent is
#: accepted.  A blocked client signature therefore looked like a rejected
#: API key and mis-verified working models.  The string identifies AMCA
#: honestly (no browser impersonation) and is accepted by those gateways.
USER_AGENT = f"AMCA-Studio/{__version__} (OpenAI-compatible client)"
