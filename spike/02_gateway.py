#!/usr/bin/env python3
"""Spike step 2: run the Mesocosm model gateway on the host.

The implementation lives in `mesocosm.gateway.app` (ADR-0003). This shim keeps the spike
runbook command stable:

    MESOCOSM_UPSTREAM=http://127.0.0.1:8000 MESOCOSM_LOG_DIR=runs/spike \
    MESOCOSM_RETURN_TOKEN_IDS=1 uv run python spike/02_gateway.py

Environment: MESOCOSM_UPSTREAM, MESOCOSM_LOG_DIR, MESOCOSM_BUDGET_JSON, MESOCOSM_RETURN_TOKEN_IDS,
MESOCOSM_GATEWAY_HOST, MESOCOSM_GATEWAY_PORT.
"""

from mesocosm.gateway.app import main

if __name__ == "__main__":
    main()
