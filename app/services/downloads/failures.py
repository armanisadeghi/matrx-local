"""Actionable download failures — the contract between a failed download and
the UI that has to help the user fix it.

A download can fail for reasons the machine cannot fix but the USER can in
about ten seconds: a Hugging Face license that hasn't been accepted, an API key
that isn't set, model weights that need the AI packages installed first. Those
are not errors to report — they are REQUESTS TO THE USER, and the app owes them
a clear explanation and a button, not a truncated red 401 string.

So every such failure carries a ``DownloadResolution``: what happened, in plain
English, plus the one action that fixes it. The engine decides the resolution
(only the engine knows *why* HF said 401); the UI only renders it and dispatches
the action. Adding a new self-fixable failure = add a constructor here and a
case in the UI's action switch — nothing else changes.

Anything WITHOUT a resolution is a genuine error: network died, disk full, the
remote 500'd. Those keep their raw message and their Retry button.
"""

from __future__ import annotations

import re as _re

from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.services.downloads.errors import NonRetryableDownloadError

ActionKind = Literal["settings_api_keys", "open_url", "install_ai_packages"]


@dataclass(frozen=True)
class DownloadResolution:
    """A user-fixable download failure and the single action that fixes it."""

    code: str
    """Stable machine id — the UI may special-case it, tests pin it."""
    title: str
    """Modal heading. Plain language, no jargon, no HTTP verbs."""
    message: str
    """What happened and why, in one or two sentences the user can act on."""
    action_kind: ActionKind
    """What the button does. The UI knows exactly these kinds."""
    action_label: str
    """The button's text."""
    action_url: str | None = None
    """Target for action_kind == "open_url"."""
    provider: str | None = None
    """Target for action_kind == "settings_api_keys" (e.g. "huggingface")."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ActionableDownloadError(NonRetryableDownloadError):
    """A download failure the user can fix. Carries the resolution the UI
    renders. Non-retryable by construction: retrying before the user acts just
    reproduces the same failure, which is precisely the loop we're killing."""

    def __init__(self, resolution: DownloadResolution) -> None:
        super().__init__(resolution.message)
        self.resolution = resolution


# ── The catalog of user-fixable failures ────────────────────────────────────


def hf_gate_not_accepted(repo_id: str) -> ActionableDownloadError:
    """HF 401/403 on a gated repo WITH a token present — the token is fine, the
    account simply hasn't accepted the model's license. This is the FLUX.1 case,
    and it is NOT a missing-key problem; telling the user to set a key they
    already set is how you make someone hate your app."""
    return ActionableDownloadError(
        DownloadResolution(
            code="hf_gate_not_accepted",
            title="This model needs your approval on Hugging Face",
            message=(
                f"Your Hugging Face account is connected, but “{repo_id}” is a "
                "gated model — its authors require you to accept their license "
                "before downloading. Open the model page, click “Agree and "
                "access repository”, then start the download again."
            ),
            action_kind="open_url",
            action_label="Accept the license on Hugging Face",
            action_url=f"https://huggingface.co/{repo_id}",
        )
    )


def hf_token_missing(repo_id: str) -> ActionableDownloadError:
    return ActionableDownloadError(
        DownloadResolution(
            code="hf_token_missing",
            title="This model needs your Hugging Face token",
            message=(
                f"“{repo_id}” is a gated model and can only be downloaded by a "
                "Hugging Face account that has access to it. Add your Hugging "
                "Face token, then start the download again."
            ),
            action_kind="settings_api_keys",
            action_label="Add your Hugging Face token",
            provider="huggingface",
        )
    )


def hf_token_invalid(repo_id: str) -> ActionableDownloadError:
    return ActionableDownloadError(
        DownloadResolution(
            code="hf_token_invalid",
            title="Hugging Face rejected your token",
            message=(
                "Hugging Face did not accept the token saved in this app — it "
                "may have been revoked or it may have expired. Replace it with "
                f"a current token, then start the “{repo_id}” download again."
            ),
            action_kind="settings_api_keys",
            action_label="Update your Hugging Face token",
            provider="huggingface",
        )
    )


def civitai_key_required() -> ActionableDownloadError:
    """Civitai 401/403 with NO key configured — the only case where 'add your
    key' is the right ask."""
    return ActionableDownloadError(
        DownloadResolution(
            code="civitai_key_required",
            title="This download needs your Civitai API key",
            message=(
                "Civitai requires an API key for this download and none is set "
                "yet. Add your Civitai key, then start the download again."
            ),
            action_kind="settings_api_keys",
            action_label="Add your Civitai key",
            provider="civitai",
        )
    )


def civitai_key_rejected() -> ActionableDownloadError:
    """Civitai 401 WITH a key attached — the key itself was refused. Telling
    the user to 'add your key' when one is already saved is how you get eight
    agents asking about a token that was set all along."""
    return ActionableDownloadError(
        DownloadResolution(
            code="civitai_key_rejected",
            title="Civitai rejected your API key",
            message=(
                "Civitai did not accept the key saved in this app — it may have "
                "been revoked or regenerated. Replace it with a current key "
                "from your Civitai account settings, then start the download "
                "again."
            ),
            action_kind="settings_api_keys",
            action_label="Update your Civitai key",
            provider="civitai",
        )
    )


def civitai_access_restricted(model_page_url: str | None = None) -> ActionableDownloadError:
    """Civitai 403 WITH a valid-looking key — the account simply doesn't have
    download rights to this file yet (early-access, membership-gated, or
    restricted). The key is fine; the fix lives on the model page."""
    return ActionableDownloadError(
        DownloadResolution(
            code="civitai_access_restricted",
            title="This model is restricted on Civitai",
            message=(
                "Your Civitai key is connected, but this file is early-access "
                "or restricted — your account doesn't have download rights to "
                "it yet. Open the model page on Civitai to unlock access, then "
                "start the download again."
            ),
            action_kind="open_url",
            action_label="Open the model page on Civitai",
            action_url=model_page_url or "https://civitai.com",
        )
    )


def hf_gate_pending(repo_id: str) -> ActionableDownloadError:
    """HF 403 on a gated repo where the user HAS requested access but approval
    is still pending (manual-review gates). Nothing is wrong with the token and
    re-accepting the license does nothing — the user just has to wait, and the
    app owes them that fact instead of an error."""
    return ActionableDownloadError(
        DownloadResolution(
            code="hf_gate_pending",
            title="Your access request is still pending",
            message=(
                f"You have already requested access to “{repo_id}”, but its "
                "authors haven't approved it yet. There is nothing to fix — "
                "check the model page for your request status and try the "
                "download again once access is granted."
            ),
            action_kind="open_url",
            action_label="Check your request on Hugging Face",
            action_url=f"https://huggingface.co/{repo_id}",
        )
    )


def ai_packages_missing() -> ActionableDownloadError:
    """The old message told the user to “Run the in-app installer (POST
    /image-gen/install)”. An HTTP verb. In end-user copy."""
    return ActionableDownloadError(
        DownloadResolution(
            code="ai_packages_missing",
            title="The AI packages aren’t installed yet",
            message=(
                "Model weights can’t be downloaded until the AI packages are "
                "installed on this machine. Install them once — it takes a few "
                "minutes — and this download will work from then on."
            ),
            action_kind="install_ai_packages",
            action_label="Install the AI packages",
        )
    )


# ── Re-triage of stale failure rows ─────────────────────────────────────────
#
# Failure rows written BEFORE this taxonomy existed carry only a raw
# ``error_msg`` (a truncated 401 string, an HTML "please log in" page, an HTTP
# verb in user copy). Every app start replayed them to the log and the UI as
# red errors. ``retriage_stale_failure`` maps a recognizable old message onto
# the taxonomy so those rows render through the same prompt UI as new failures.
# Unrecognizable messages return None and stay what they are: real errors.

_HF_GATED_PATTERNS = (
    "access to model",          # "Access to model X is restricted"
    "gated repo",               # GatedRepoError text / "Cannot access gated repo"
    "restricted. you must have access",
    "please log in",            # unauthenticated variant of the gate message
    "must be authenticated",
)
_HF_HOST_MARKERS = ("huggingface.co", "hf.co", "hugging face")
_AI_PACKAGES_PATTERNS = (
    "run the in-app installer",            # the old pre-taxonomy message
    "huggingface_hub is required",
    "no module named 'huggingface_hub'",
    "no module named 'diffusers'",
    "ai packages",
)


def _repo_id_from_text(text: str) -> str | None:
    m = _re.search(r"(?:huggingface\.co/|hf\.co/|api/models/|Access to model )"
                   r"([\w.\-]+/[\w.\-]+)", text)
    return m.group(1).rstrip(".,:") if m else None


def retriage_stale_failure(
    error_msg: str | None,
    metadata: dict[str, Any] | None,
    *,
    hf_token_present: bool,
    civitai_key_present: bool,
) -> DownloadResolution | None:
    """Map a pre-taxonomy failure row's raw error text onto the resolution
    catalog, or None when the message isn't a recognizable user-fixable state.

    Attribution mirrors the live classifiers: an HF gate failure with a token
    configured NOW asks for license acceptance, never for the token the user
    already set; without a token it asks for the token. (No network calls here
    — this runs during startup hydration.)
    """
    if not error_msg:
        return None
    low = error_msg.lower()
    md = metadata or {}

    if any(p in low for p in _AI_PACKAGES_PATTERNS):
        return ai_packages_missing().resolution

    if md.get("civitai_download") and (
        "401" in low or "403" in low or "unauthorized" in low or "forbidden" in low
    ):
        if not civitai_key_present:
            return civitai_key_required().resolution
        if "403" in low or "forbidden" in low:
            return civitai_access_restricted(md.get("model_page_url")).resolution
        return civitai_key_rejected().resolution

    is_hf = bool(md.get("hf_repo_id")) or any(h in low for h in _HF_HOST_MARKERS)
    looks_gated = any(p in low for p in _HF_GATED_PATTERNS) or (
        is_hf and ("401" in low or "403" in low)
    )
    if is_hf and looks_gated:
        repo_id = str(md.get("hf_repo_id") or _repo_id_from_text(error_msg)
                      or "this model")
        if not hf_token_present:
            return hf_token_missing(repo_id).resolution
        if "awaiting" in low or "pending" in low:
            return hf_gate_pending(repo_id).resolution
        return hf_gate_not_accepted(repo_id).resolution

    return None
