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
    return ActionableDownloadError(
        DownloadResolution(
            code="civitai_key_required",
            title="This download needs your Civitai API key",
            message=(
                "Civitai refused the download because no valid API key is set. "
                "Add your Civitai key, then start the download again."
            ),
            action_kind="settings_api_keys",
            action_label="Add your Civitai key",
            provider="civitai",
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
