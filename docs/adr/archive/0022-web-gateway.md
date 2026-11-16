# ADR-0022: The real internet reaches the sandbox only through a web gateway

Status: accepted · Date: 2026-10-18

## Context

The sandbox had no route out but the model gateway (ADR-0003), and the agents' "internet" was the
world services: a mock package index, mock sites, a wiki. That is right for isolation and wrong for
realism: deployed agents look things up, read documentation, fetch packages, and part of what the
research wants to see is what they do with that reach. The rule in AGENTS.md ("any outbound route
from the sandbox network other than the gateway" is forbidden) was written when the gateway was the
only chokepoint we had. The requirement has not changed: everything an agent receives must be
recorded, and everything it may reach must be decided by the control plane, not by the agent.

## Decision

- **A second gateway, same kind as the first.** `internet.enabled` adds a `web` container (mitmproxy
  with our addon, `spike/docker/web_addon.py`) attached to every sandbox network as `proxy`
  (address `.5`) and to `edge`. It is the only container with both an agent-facing address and a
  route out. Agents reach it as any workstation reaches a corporate proxy: `HTTP_PROXY`,
  `HTTPS_PROXY`, `NO_PROXY` for the sandbox hostnames, and the CA bundle variables
  (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `PIP_CERT`, `GIT_SSL_CAINFO`,
  `CURL_CA_BUNDLE`) pointing at the proxy's CA certificate, mounted read-only. Nothing of ours is in
  that environment (ADR-0019). The CA is generated once per run label by the control plane
  (`openssl`, `runs/<label>/web/ca/`): the key stays in the proxy's store, the certificate goes to
  the agents.
- **Policy, decided per request at the proxy.** `internet.allow` and `internet.deny` are host globs;
  `deny` wins; everything not allowed is refused. Private, loopback, link-local and multicast
  addresses, the sandbox hostnames, unqualified names, local domains and the world's own domain are
  refused whatever the lists say (`mesocosm.env.web.network_denied`), so the proxy is never a path to
  the inference server, the control plane or another agent. Only ports 80 and 443. An HTTPS host is
  refused at CONNECT, before any TLS, so nothing is sent to a denied host. Refusals are a web
  filter's page naming the host and a reference code, never the platform. `internet.budget`
  (requests, response bytes per agent) refuses with the same page when spent.
- **Everything is recorded.** `web.jsonl` has one line per request: agent (by the fixed address, as
  at the world services), method, scheme, host, port, path, decision and the rule that decided it,
  status, sizes, content type, the response body's SHA-256 and, for text, a 2,000-character excerpt.
  `internet.mode: record` additionally saves every flow (`-w flows.mitm`) so a run can be replayed
  without the internet (`mode: replay`, `replay_file`, unmatched requests killed). The run manager
  copies this run's lines into the bundle (`native/web/web.jsonl`) and records `web.request` events;
  the judge flags refused requests (`blocked_egress`) as candidate incidents for a verdict.
- **A condition with a receipt.** The proxy writes `policy.json` at startup; before the run the run
  manager compares it with the manifest and requires every agent's egress check to confirm the proxy
  present and policing (a denied host answers 403, its CONNECT is refused, an allowed host is
  reachable through the proxy's CA) or, with the internet disabled, absent. Receipt
  `conditions.set internet`; mismatch aborts with `failed_internet_verification`. Direct paths stay
  dead: the check clears the proxy variables before probing them.
- **The rule changes.** AGENTS.md now forbids any outbound route other than the two gateways. Still
  forbidden: `network_mode: host`, a second NIC on an agent container, any route that bypasses the
  web gateway's policy and log.

## Consequences

- Agents can read the real web, install from the real PyPI (when allowed) and fetch documentation;
  what they read is in the bundle with the same identity join as the board and the model prompts.
- TLS is terminated at the proxy with certificates from our CA. Clients that pin certificates or
  ignore the CA variables fail; that is the expected behavior of a corporate proxy, and it is logged.
  Non-HTTP protocols (ssh, raw TCP) have no route; `git` over HTTPS works through the proxy.
- The proxy's upstream connections leave through the Docker host. On Colab rootless Docker that is
  the slirp4netns route the gateway already uses for vLLM.
- `web.jsonl` and `flows.mitm` are not in a snapshot (NOT_CAPTURED); replay needs the flows file
  kept with the manifest.
- Replay fidelity is mitmproxy's: requests are matched on method, host and path; a changed query
  or body is killed, not forwarded. Good enough for a repeatable smoke run, not for a crawl.
