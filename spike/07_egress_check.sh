#!/bin/sh
# Spike step 7: prove the agent container cannot reach the outside world.
# Run INSIDE an agent container:  docker compose -f spike/docker/compose.yml exec agent-1 egress-check
#
# Exit 0 only if the gateway is reachable AND every outbound path fails.
# DNS resolution is reported separately: Docker's embedded DNS may still resolve
# external names on an internal network, which is itself a covert channel
# (DNS exfiltration). If it resolves, M1 must add a DNS sinkhole.

GW="${MESOCOSM_GATEWAY_URL:-http://gateway:8080}"
UPSTREAM_PROBE="${MESOCOSM_UPSTREAM_PROBE:-http://host.docker.internal:8000/v1/models}"
fail=0
warn=0

say() { printf '%-5s %s\n' "$1" "$2"; }

if curl -fsS --max-time 5 "$GW/mesocosm/health" >/dev/null 2>&1; then
  say OK "gateway reachable at $GW"
else
  say FAIL "gateway NOT reachable at $GW"; fail=1
fi

if curl -sS --max-time 5 -o /dev/null https://example.com 2>/dev/null; then
  say FAIL "HTTPS egress to example.com succeeded"; fail=1
else
  say OK "HTTPS egress blocked"
fi

if curl -sS --max-time 5 -o /dev/null http://1.1.1.1 2>/dev/null; then
  say FAIL "HTTP egress to a raw IP succeeded"; fail=1
else
  say OK "raw-IP egress blocked"
fi

if ping -c1 -W2 1.1.1.1 >/dev/null 2>&1; then
  say FAIL "ICMP egress succeeded"; fail=1
else
  say OK "ICMP egress blocked (or ping unavailable)"
fi

if getent hosts example.com >/dev/null 2>&1; then
  say WARN "external DNS name resolved (covert channel; add DNS sinkhole in M1)"; warn=1
else
  say OK "external DNS does not resolve"
fi

if curl -sS --max-time 3 -o /dev/null "$UPSTREAM_PROBE" 2>/dev/null; then
  say FAIL "inference server reachable directly (must only be reachable via gateway)"; fail=1
else
  say OK "inference server not directly reachable"
fi

if [ "$fail" -eq 0 ]; then
  [ "$warn" -eq 0 ] && say PASS "isolation check" || say PASS "isolation check (with warnings)"
else
  say FAIL "isolation check"
fi
exit $fail
