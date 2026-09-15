# Linux container isolation probe

Run only the disposable boundary control:

```bash
python3 scripts/reliability-container-isolation.py --revision <committed-revision>
```

For a reviewed uncommitted prerequisite, bind it explicitly:

```bash
python3 scripts/reliability-container-isolation.py --revision <committed-revision> \
  --patch <reviewed.patch> --patch-sha256 <sha256>
```

It uses the already-local Linux/arm64 Python 3.13 image ID
`sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285`.
No image is pulled or built.
The script archives only the named committed revision; an uncommitted change is
allowed only with `--patch` and a matching `--patch-sha256`. It mounts that
agent-owned snapshot read-only at `/workspace` and mounts no host home, Docker
socket, configuration, cache, or virtual environment.

The container has no network, a read-only root filesystem, an unprivileged UID,
no Linux capabilities, no-new-privileges, PID/CPU/memory limits, and only
private tmpfs writable roots. The probe proves a private write/read, read-only
rootfs denial, internal loopback success, an external UDP connect denied with
`ENETUNREACH`/`EACCES`/`EPERM`, absence of a host-only disposable sentinel, and
inherited child denials. Its receipt binds the source, image, launcher, probe,
and container-profile digests plus Docker inspection metadata. It exits nonzero
unless every requested Docker limit and mount restriction is present in that
inspection.

This establishes Linux Python boundary behavior only. It cannot certify macOS,
Tauri, Keychain, TCC, packaging, signing, updater, or installed-app behavior.
