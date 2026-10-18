#!/bin/sh
# Spike step 7: prove the agent container cannot reach the outside world.
# Piped into an agent container by the run manager (mesocosm.env.egress), never installed in the image:
#   docker compose -f <compose> exec -T -e MESOCOSM_SERVICES=... agent-1 sh -s < spike/07_egress_check.sh
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

if curl -fsS --max-time 5 "$GW/health" >/dev/null 2>&1; then
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

# Other agents' harness servers (MESOCOSM_PEERS: comma-separated agent ids). With isolate_agents on,
# reaching a peer is a failure; on a shared sandbox it is expected.
for peer in $(printf '%s' "${MESOCOSM_PEERS:-}" | tr ',' ' '); do
  if curl -sS --max-time 3 -o /dev/null "http://$peer:4096/global/health" 2>/dev/null; then
    if [ "${MESOCOSM_ISOLATE_AGENTS:-0}" = "1" ]; then
      say FAIL "peer $peer reachable although isolate_agents is on"; fail=1
    else
      say OK "peer $peer reachable (shared sandbox)"
    fi
  else
    if [ "${MESOCOSM_ISOLATE_AGENTS:-0}" = "1" ]; then
      say OK "peer $peer not reachable (isolate_agents)"
    else
      say WARN "peer $peer not reachable on the shared sandbox"; warn=1
    fi
  fi
done

# World services (MESOCOSM_SERVICES: comma-separated hostnames) must answer.
for svc in $(printf '%s' "${MESOCOSM_SERVICES:-}" | tr ',' ' '); do
  if curl -fsS --max-time 3 -o /dev/null "http://$svc/" 2>/dev/null; then
    say OK "service $svc reachable"
  else
    say FAIL "service $svc NOT reachable"; fail=1
  fi
done

# Hidden channels (MESOCOSM_CHANNELS): an enabled channel must exist, a disabled one must not.
case ",${MESOCOSM_CHANNELS:-}," in
  *,pastebin,*)
    if curl -fsS --max-time 3 -o /dev/null "http://pastebin/" 2>/dev/null; then
      say OK "channel pastebin reachable (enabled)"
    else
      say FAIL "channel pastebin NOT reachable although enabled"; fail=1
    fi ;;
  *)
    if curl -sS --max-time 3 -o /dev/null "http://pastebin/" 2>/dev/null; then
      say FAIL "channel pastebin reachable although disabled"; fail=1
    else
      say OK "channel pastebin absent (disabled)"
    fi ;;
esac
case ",${MESOCOSM_CHANNELS:-}," in
  *,shared_dir,*)
    if [ -d /shared ] && [ -w /shared ]; then
      say OK "channel shared_dir mounted (enabled)"
    else
      say FAIL "channel shared_dir NOT mounted although enabled"; fail=1
    fi ;;
  *)
    if [ -d /shared ]; then
      say FAIL "channel shared_dir mounted although disabled"; fail=1
    else
      say OK "channel shared_dir absent (disabled)"
    fi ;;
esac

if [ "$fail" -eq 0 ]; then
  [ "$warn" -eq 0 ] && say PASS "isolation check" || say PASS "isolation check (with warnings)"
else
  say FAIL "isolation check"
fi
exit $fail
