"""L0 scaffolding tests.

Step 1: package skeleton and brand constants.
Step 2 (logger) and Step 3 (support files) tests are added in their own
code-implementer invocations.
"""
import tomllib
from pathlib import Path

import agent
import agent.__about__ as __about__

PROJECT_ROOT = Path(__file__).parent.parent


# --- Step 1: package skeleton and brand constants ---


def test_agent_importable():
    """agent package must import without error."""
    # Import already happened at module load; verify the module object exists.
    assert agent is not None


def test_about_constants():
    """__brand__ must be a non-empty string; __version__ must start with '0.1'."""
    assert isinstance(__about__.__brand__, str) and __about__.__brand__
    assert __about__.__version__.startswith("0.1")


def test_brand_not_in_init_source():
    """Brand string must not appear in agent/__init__.py source text.

    Proxy check for NFR-BD1 (the real grep check runs in L6).
    Reads __init__.__file__ so the path stays correct regardless of install mode.
    """
    init_path = agent.__file__
    with open(init_path, encoding="utf-8") as fh:
        source = fh.read()
    assert __about__.__brand__ not in source, (
        f"Brand string '{__about__.__brand__}' must not appear in agent/__init__.py"
    )


def test_no_provider_sdk_is_a_hard_dependency():
    """No provider-specific SDK should be a hard dependency.

    Verifies the agnostic guarantee: installing our project does NOT force any
    specific LLM provider's SDK on the user. Providers are configured at runtime
    via config.yaml; the SDK that pydantic-ai uses to talk to them is generic.

    Concretely: our pyproject.toml must not list provider-vendor SDKs (anthropic,
    openai, google-generativeai, etc.) as hard dependencies. If any are needed
    for a specific provider integration, they belong in optional `[project.optional-dependencies]`
    extras (e.g. `nanoclawfork[anthropic]`) — never in the base install.
    """
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    declared = [d.lower() for d in pyproject["project"]["dependencies"]]

    forbidden_in_base = ["anthropic", "openai", "google-generativeai", "cohere", "mistralai"]
    for sdk in forbidden_in_base:
        assert not any(d.startswith(sdk) for d in declared), (
            f"{sdk!r} must not be a hard dependency — it would force this provider on every user. "
            f"If needed for an optional integration, put it in [project.optional-dependencies]."
        )
