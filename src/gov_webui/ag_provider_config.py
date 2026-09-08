# SPDX-License-Identifier: Apache-2.0
"""Build strict ag-providerd/providerctl policy from Marginalia's model catalog."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from gov_webui.model_providers import ConfiguredModel, load_provider_catalog


_SAFE_ENVIRONMENT = {
    "LANG",
    "LC_ALL",
}


def _toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _credential_name(environment_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", environment_name.lower()).strip("-")


def _endpoint_url(model: ConfiguredModel) -> str:
    assert model.base_url is not None
    suffix = "/messages" if model.protocol == "anthropic-messages" else "/chat/completions"
    return model.base_url.rstrip("/") + suffix


def _command(
    model: ConfiguredModel, environment: Mapping[str, str]
) -> tuple[str, str, str, dict[str, str]]:
    if model.protocol == "existing-command":
        adapter = "codex"
        executable = environment.get("CODEX_NATIVE_PATH", "/opt/codex/codex")
        working_directory = environment.get(
            "CODEX_COMMAND_WORKDIR", "/var/lib/marginalia/provider-work"
        )
    else:
        assert model.command is not None
        adapter = model.command.adapter
        executable = environment.get(model.command.executable_env, "").strip()
        working_directory = environment.get(model.command.working_directory_env, "").strip()
    if not Path(executable).is_absolute() or not Path(working_directory).is_absolute():
        raise ValueError(
            f"provider {model.provider_id!r} command executable and working directory "
            "must be absolute"
        )
    child_environment = {
        key: value
        for key, value in environment.items()
        if key in _SAFE_ENVIRONMENT and value and "\x00" not in value
    }
    # Host paths are never copied into container policy. Provider authentication
    # lives in the provider daemon's private, explicitly mounted home.
    child_environment.update(
        {
            "HOME": "/var/lib/marginalia/provider-home",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "TMPDIR": "/tmp",
            "CODEX_HOME": "/var/lib/marginalia/provider-home/.codex",
            "CLAUDE_CONFIG_DIR": "/var/lib/marginalia/provider-home/.claude",
            "KIMI_CONFIG_DIR": "/var/lib/marginalia/provider-home/.kimi",
        }
    )
    return adapter, executable, working_directory, child_environment


def render_provider_configs(
    model_config: Path,
    identity_metadata: Path,
    *,
    environment: Mapping[str, str] | None = None,
    uid: int = 0,
    gid: int = 0,
    authority_domain: str = "marginalia.local",
    epoch: str = "1",
) -> tuple[str, str]:
    """Return matching root-owned daemon/client policy; never read secret bodies."""
    env = os.environ if environment is None else environment
    catalog = load_provider_catalog(model_config)
    identities = json.loads(identity_metadata.read_text(encoding="utf-8"))
    if identities.get("schema") != "marginalia.ag-ng-provider-identities/v1":
        raise ValueError("unsupported provider identity metadata")
    providerd = identities["providerd"]
    providerctl = identities["providerctl"]

    grouped: dict[str, list[ConfiguredModel]] = {}
    for model in catalog.models:
        grouped.setdefault(model.provider_id, []).append(model)

    daemon = [
        'schema = "ag.config.providerd.v2"',
        'security_profile = "production"',
        f"authority_domain = {_toml(authority_domain)}",
        f"epoch = {_toml(epoch)}",
        'database = "/var/lib/agent-governor/providerd/providerd.db"',
        'object_store = "/var/lib/agent-governor/providerd/objects"',
        'socket = "/run/marginalia/providerd/provider.sock"',
        "",
        "[store_custody.database_parent]",
        f"uid = {uid}",
        f"gid = {gid}",
        "mode = 0o700",
        "[store_custody.object_store]",
        f"uid = {uid}",
        f"gid = {gid}",
        "mode = 0o700",
        "[store_custody.database]",
        f"uid = {uid}",
        f"gid = {gid}",
        "mode = 0o600",
        "[store_custody.writer_lock]",
        f"uid = {uid}",
        f"gid = {gid}",
        "mode = 0o600",
        "[socket_custody.parent]",
        f"uid = {uid}",
        f"gid = {gid}",
        "mode = 0o2750",
        "[socket_custody.node]",
        f"uid = {uid}",
        f"gid = {gid}",
        "mode = 0o660",
        "",
        "[rpc_signing_identity]",
        f"principal = {_toml(providerd['principal'])}",
        f"key_id = {_toml(providerd['key_id'])}",
        f"public_key = {_toml(providerd['public_key'])}",
        'private_key_credential = "/run/credentials/marginalia-providerd/rpc.pk8"',
        "[caller_peer]",
        'role = "marginalia_generation_executor"',
        f"uid = {uid}",
        f"gid = {gid}",
        f"stable_principal_root = {_toml(providerctl['principal'])}",
        'principal_kind = "service"',
        "[caller_peer.rpc_key]",
        f"principal = {_toml(providerctl['principal'])}",
        f"key_id = {_toml(providerctl['key_id'])}",
        f"public_key = {_toml(providerctl['public_key'])}",
        "maximum_clock_skew_ms = 30000",
        "[limits]",
        "max_control_frame_bytes = 94371840",
        "max_concurrent_requests = 4",
        "max_request_bytes = 16777216",
        "max_response_bytes = 67108864",
        "max_rpc_replay_entries = 4096",
        "provider_deadline_ms = 1800000",
    ]
    for provider_id, models in grouped.items():
        sample = models[0]
        daemon.extend(["", "[[endpoints]]", f"id = {_toml(provider_id)}"])
        if sample.protocol in {"openai-compatible", "anthropic-messages"}:
            parsed = urlsplit(_endpoint_url(sample))
            plaintext = parsed.scheme == "http"
            if plaintext and sample.inference != "local":
                raise ValueError(
                    f"provider {provider_id!r}: plaintext HTTP is admitted only for "
                    "providers declared local"
                )
            credential = _credential_name(sample.api_key_env) if sample.api_key_env else ""
            if not plaintext and not credential:
                raise ValueError(
                    f"provider {provider_id!r}: remote HTTPS API requires an enrolled credential"
                )
            header = "x-api-key" if sample.protocol == "anthropic-messages" else "authorization"
            prefix = "" if sample.protocol == "anthropic-messages" else "Bearer "
            daemon.extend(
                [
                    'protocol = "opaque_json_v1"',
                    f"methods = [{_toml('messages.create' if sample.protocol == 'anthropic-messages' else 'chat.completions.create')}]",
                    "[endpoints.transport]",
                ]
            )
            if plaintext:
                origin = f"{parsed.scheme}://{parsed.netloc}"
                daemon.extend(
                    [
                        'kind = "local_http"',
                        f"url = {_toml(_endpoint_url(sample))}",
                        f"allowed_origins = [{_toml(origin)}]",
                        'redirect_policy = "deny"',
                    ]
                )
            else:
                daemon.extend(
                    [
                        'kind = "credentialed_https_api"',
                        f"url = {_toml(_endpoint_url(sample))}",
                        f"credential_name = {_toml(credential)}",
                        f"credential_header = {_toml(header)}",
                        f"credential_prefix = {_toml(prefix)}",
                    ]
                )
            if sample.protocol == "anthropic-messages":
                daemon.extend(["[endpoints.headers]", 'anthropic-version = "2023-06-01"'])
        else:
            adapter, executable, working_directory, child_environment = _command(sample, env)
            daemon.extend(
                [
                    'protocol = "opaque_json_v1"',
                    'methods = ["command.complete"]',
                    "[endpoints.transport]",
                    'kind = "command"',
                    "[endpoints.transport.command]",
                    f"adapter = {_toml(adapter)}",
                    f"executable = {_toml(executable)}",
                    f"working_directory = {_toml(working_directory)}",
                    "[endpoints.transport.command.environment]",
                ]
            )
            for name, value in sorted(child_environment.items()):
                daemon.append(f"{name} = {_toml(value)}")
        # Several Erin-facing selections may intentionally resolve to the same
        # provider/model pair (for example two Claude presets). ag-ng policy is
        # keyed by the physical backend model, so emit that policy once while
        # preserving every selection in providers.json.
        physical_models = {model.model_id: model for model in models}
        for model in physical_models.values():
            daemon.extend(
                [
                    "[[endpoints.models]]",
                    f"id = {_toml(model.model_id)}",
                    "max_event_stream_bytes = 67108864",
                    "worst_case_cost_microunits = 1000000000",
                ]
            )

    client = [
        'schema = "ag.config.providerctl.v1"',
        'providerd_socket = "/run/marginalia/providerd/provider.sock"',
        'providerd_policy_config = "/etc/marginalia/providerd.toml"',
        "[rpc_signing_identity]",
        f"principal = {_toml(providerctl['principal'])}",
        f"key_id = {_toml(providerctl['key_id'])}",
        f"public_key = {_toml(providerctl['public_key'])}",
        'private_key_credential = "/run/credentials/marginalia-generation-worker/rpc.pk8"',
        "[providerd_peer.rpc_key]",
        f"principal = {_toml(providerd['principal'])}",
        f"key_id = {_toml(providerd['key_id'])}",
        f"public_key = {_toml(providerd['public_key'])}",
        "maximum_clock_skew_ms = 30000",
        "[providerd_peer.socket_peer]",
        'mode = "observe_only"',
        "[limits]",
        "max_control_frame_bytes = 94371840",
        "rpc_replay_capacity = 4096",
    ]
    return "\n".join(daemon) + "\n", "\n".join(client) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_config", type=Path)
    parser.add_argument("identity_metadata", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--authority-domain", default="marginalia.local")
    parser.add_argument("--epoch", default="1")
    arguments = parser.parse_args()
    try:
        providerd, providerctl = render_provider_configs(
            arguments.model_config,
            arguments.identity_metadata,
            authority_domain=arguments.authority_domain,
            epoch=arguments.epoch,
        )
        arguments.output_directory.mkdir(parents=True, exist_ok=True)
        for name, content in (("providerd.toml", providerd), ("providerctl.toml", providerctl)):
            path = arguments.output_directory / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, content.encode())
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception as exc:
        sys.stderr.write(f"provider config creation refused: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
