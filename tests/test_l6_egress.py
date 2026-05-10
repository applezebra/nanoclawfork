"""L6 egress allowlist tests — runs against a live docker-compose stack.

These tests verify SECURITY.md control 10b end-to-end. They are the egress
pen-test: every PR runs them in CI and they fail the build if the proxy
filter or the network isolation regresses.

Marked `@pytest.mark.docker` so they are skipped on local runs without a
compose stack. The CI workflow (.github/workflows/egress-test.yml) brings
the stack up before invoking pytest with `-m docker`.

Probes run inside the bot container using python3 + httpx (both already
present in the runtime image). curl is intentionally NOT installed in the
runtime image (SECURITY.md control 16: minimal base).
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap

import pytest

# Container name follows compose's default: <project>-<service>-<replica>.
# The egress-test workflow uses `-p kayaclaw` so this is stable.
_BOT_CONTAINER = "kayaclaw-agent-1"


def _py_in_agent(code: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run a Python snippet inside the bot container and capture output.

    httpx automatically picks up the HTTPS_PROXY/HTTP_PROXY env vars set
    in compose, so unless `trust_env=False` is passed in the snippet the
    request is routed through the proxy sidecar.
    """
    return subprocess.run(
        ["docker", "exec", _BOT_CONTAINER, "python3", "-c", code],
        capture_output=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module", autouse=True)
def _stack_required() -> None:
    """Skip the whole module unless docker is on PATH AND the bot
    container is running. Lets local `pytest` invocations pass cleanly
    without bringing up a compose stack first; CI's egress-test workflow
    starts the stack before pytest runs.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker not available on this host", allow_module_level=True)
    probe = subprocess.run(
        ["docker", "inspect", _BOT_CONTAINER],
        capture_output=True,
    )
    if probe.returncode != 0:
        pytest.skip(
            f"compose stack not running ({_BOT_CONTAINER} not found); "
            f"start with: docker compose -p kayaclaw up -d --build --wait",
            allow_module_level=True,
        )


pytestmark = pytest.mark.docker


# Common probe template: HEAD via httpx (default trust_env=True so HTTPS_PROXY
# is honored). For an allowed host, the proxy forwards and httpx returns a
# Response; the test only cares that the proxy let traffic through, not what
# upstream said (a 401/403 from upstream is still a successful proxy traversal).
# For a denied HTTPS host, tinyproxy refuses the CONNECT tunnel with 403 and
# httpx surfaces that as ProxyError. For a denied plain-HTTP host, tinyproxy
# returns a 403 response directly. The probe prints one of:
#   "OK <status>"        — proxy forwarded, upstream answered (any code)
#   "PROXY_ERR <msg>"    — tinyproxy refused (CONNECT denial includes "403")
#   "ERR <Type>: <msg>"  — transport failure (DNS, no route, timeout)
# Exit code 0 always; tests assert on stdout content.
_PROBE_VIA_PROXY = textwrap.dedent("""
    import sys, httpx
    url = sys.argv[1]
    try:
        r = httpx.head(url, timeout=5, follow_redirects=False)
        print(f"OK {r.status_code}")
    except httpx.ProxyError as e:
        print(f"PROXY_ERR {e}")
    except Exception as e:
        print(f"ERR {type(e).__name__}: {e}")
""").strip()

# Probe that bypasses the proxy entirely (trust_env=False). Used to verify
# that agent-net's `internal: true` blocks direct external traffic at the
# kernel layer, independently of the tinyproxy filter.
_PROBE_DIRECT = textwrap.dedent("""
    import sys, httpx
    url = sys.argv[1]
    try:
        r = httpx.get(url, timeout=5, follow_redirects=False, trust_env=False)
        print(f"OK {r.status_code}")
    except Exception as e:
        print(f"ERR {type(e).__name__}: {e}")
""").strip()


def _run_probe(script: str, url: str, timeout: int = 15) -> subprocess.CompletedProcess:
    # `python3 -c "<script>" <url>` makes sys.argv == ["-c", url]; the
    # snippet reads sys.argv[1].
    return subprocess.run(
        ["docker", "exec", _BOT_CONTAINER, "python3", "-c", script, url],
        capture_output=True,
        timeout=timeout,
    )


def test_allowlist_allows_telegram() -> None:
    """The allowlisted Telegram API host is reachable through the proxy."""
    result = _run_probe(_PROBE_VIA_PROXY, "https://api.telegram.org")
    out = result.stdout.decode(errors="replace")
    assert out.startswith("OK "), (
        f"Telegram should be reachable through the proxy. stdout={out!r} "
        f"stderr={result.stderr.decode(errors='replace')!r}"
    )


def test_proxy_denies_unauthorized_host_with_403() -> None:
    """A non-allowlisted hostname returns proxy 403, NOT a generic timeout."""
    result = _run_probe(_PROBE_VIA_PROXY, "https://evil.example.com")
    out = result.stdout.decode(errors="replace")
    # tinyproxy denies the CONNECT tunnel with 403 for HTTPS; httpx surfaces
    # that as a ProxyError whose message includes the status line. Asserting
    # on "403" (not just non-zero) ensures the test does NOT silently pass on
    # DNS failure or timeout.
    assert "403" in out, (
        f"Expected proxy 403 for evil.example.com; stdout={out!r} "
        f"stderr={result.stderr.decode(errors='replace')!r}"
    )


def test_proxy_denies_ip_literal_bypass() -> None:
    """An IP literal request through the proxy is denied (no IPs allowlisted).

    Plain HTTP (not HTTPS) so tinyproxy's filter denial comes back as a
    direct 403 response rather than a CONNECT failure — easier to assert on.
    """
    result = _run_probe(_PROBE_VIA_PROXY, "http://1.1.1.1")
    out = result.stdout.decode(errors="replace")
    assert "403" in out, (
        f"Expected proxy 403 for 1.1.1.1; stdout={out!r} "
        f"stderr={result.stderr.decode(errors='replace')!r}"
    )


def test_network_isolation_blocks_proxy_bypass() -> None:
    """With agent-net internal: true, bypassing the proxy must fail at the
    kernel layer (no route), not at the proxy filter. This proves the
    network-level guarantee independently of tinyproxy.
    """
    result = _run_probe(_PROBE_DIRECT, "http://1.1.1.1")
    out = result.stdout.decode(errors="replace").lower()
    assert out.startswith("err "), (
        "internal: true must block direct external traffic from the bot "
        f"(trust_env=False request should fail). stdout={out!r}"
    )
    # Connection-failure markers, not "403" (which would mean we hit the proxy).
    failure_markers = (
        "network is unreachable", "no route", "connection refused",
        "connect", "timed out", "timeout", "unreachable", "connecterror",
    )
    assert any(m in out for m in failure_markers), (
        f"expected a connection-failure marker; got stdout={out!r}"
    )


def test_allowlist_allows_configured_llm_provider() -> None:
    """The CI test config sets base_url to openrouter.ai, so that host
    must be reachable through the proxy (validates dynamic allowlist
    generation from config.yaml).
    """
    result = _run_probe(_PROBE_VIA_PROXY, "https://openrouter.ai")
    out = result.stdout.decode(errors="replace")
    assert out.startswith("OK "), (
        f"Configured LLM provider host should be reachable. stdout={out!r} "
        f"stderr={result.stderr.decode(errors='replace')!r}"
    )
