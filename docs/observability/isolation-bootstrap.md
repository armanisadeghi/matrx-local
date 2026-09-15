# Reliability isolation bootstrap

This is a narrow, outer boundary for the first resolver and pure-Python unit
subprocesses. It is not a certificate for the engine, Vite, Tauri, a browser,
or an installed app.

Run the disposable capability check first:

```bash
python3 scripts/reliability-isolation.py probe
```

The launcher builds a tracked `git archive` of one committed revision in a private scratch
directory, records its commit plus archive and extracted-tree SHA-256 digests, resolves the
Xcode-selected Python with `xcrun --find python3` before confinement, and starts that exact
runtime under `sandbox-exec`. Untracked files, local changes, and `.env` are absent from
that archive. The confined process receives a newly constructed allowlist
environment: all Matrx roots and the port identity point into its run directory;
credentials, proxies, arbitrary Matrx overrides, `HOME`, and `CODEX_HOME` are
not inherited or replaced.

The probe uses only disposable paths. It must prove a write/read positive
control in the private run root, denied read and write against a disposable
sibling outside that root, denied loopback network access, and inherited denial
in a child Python process. It writes a bounded JSON receipt path to stdout. A
nonzero exit is a failed capability probe, never a certification.

On current macOS, dyld requires literal read access to the root vnode before it
can execute a confined binary. The profile grants `(literal "/")` only after
its protected-path denies; that reveals root entry names but no subtree
contents. This is the documented Seatbelt behavior in
[sandbox-runtime issue 190 handling](https://raw.githubusercontent.com/anthropic-experimental/sandbox-runtime/main/src/sandbox/macos-sandbox-utils.ts#L661-L667).

After an independent reviewer accepts that receipt, the same boundary may run a
bounded pure-Python resolver or unit subprocess from the snapshot:

```bash
python3 scripts/reliability-isolation.py run -- -c 'print("resolver fixture")'
```

Use a named committed revision when a repair is ready. A reviewed uncommitted
diff is permitted only when supplied explicitly with its SHA-256; it is applied
to the clean archive, never taken implicitly from the shared worktree:

```bash
python3 scripts/reliability-isolation.py --revision <commit> --patch /path/reviewed.patch --patch-sha256 <sha256> probe
```

Only use this lane for source-level resolver/unit work. It intentionally denies
network and Keychain access and must not be used for an app import, engine
startup, pytest fixture that launches the engine, Vite, Tauri, browser, or UI
test. Those lanes require a later certificate showing native-platform fidelity,
including Keychain, TCC, app-support/preferences, process ownership, and
permitted local-loopback behavior. The bootstrap also does not repair product
isolation defects such as writable shared caches or ungated cloud-sync paths.

Each result is tied to the generated snapshot digest and has a 20-second outer
timeout. If the capability test cannot prove both the positive and negative
controls on this Mac, stop active local testing and use a dedicated host rather
than treating the profile label as evidence.


## Independent acceptance — September 15, 2026

Zero-authorship Sol accepted the resolver-only outer boundary after executing the
actual disposable probe on the native host runner. Its first attempt inside an
additional CLI Seatbelt sandbox could not apply a nested profile and did not
count as proof; the independent native-host rerun passed with the candidate
profile unchanged.

Durable receipt:
`common-docs/projects/unified-error-observability/matrx-local/ISOLATION-RESOLVER-RECEIPT-2026-09-15.json`

- Receipt SHA-256: `554368d9cac50bba1ebddf75e7f24afa842166f7cfd4b5a00648592bdb13a4fd`
- Launcher SHA-256: `a2d05fea1506ecd15aff45c1e5597ade708bda8157ebedd8d1db4576b4aa9d29`
- Profile SHA-256: `175fe8381c70c66a43afeee6c4d0a110fb754f3114975558fdaf930d6bf1f768`
- Python SHA-256: `6e7ae61f68a3838094fc56590f84c52069a97d7816f6bb79e0e85995b340e464`

Private-root read/write succeeded. Existing forbidden sibling and its symlink
alias both refused reads/writes with EPERM; loopback bind refused with EPERM;
the child interpreter demonstrably ran and inherited denial. The parent checked
that forbidden bytes remained unchanged. The receipt binds the source archive
and extracted snapshot as well as these exact boundary/runtime bytes.

Accepted scope is Python resolver/pure-stdlib work only. Use `--revision` and,
for Agent 3's separately reviewed source prerequisite, `--patch` with the exact
`--patch-sha256` recorded in its durable bootstrap manifest. This certificate
does not accept app imports, startup smoke, dependencies outside this runtime,
native/UI/installed tests, or Windows/Linux isolation. Changes to the launcher,
profile, runtime, or supported capability require independent re-verification.
