# `desktop/vendor/` — packed tarballs of `@ai-matrx/*` packages awaiting their first npm publish

**Nothing lives here permanently.** A tarball here bridges "the shared package
is built and tested" to "the shared package is on the npm registry". Every
entry has a named human step that removes it.

| Tarball | Package | Removed when |
|---|---|---|
| `ai-matrx-content-ir-react-0.1.0.tgz` | `@ai-matrx/content-ir-react` 0.1.0 | the package is publishable on npm — see below |

A `link:` to `../../aidream/apps/shared/content-ir-react/` would resolve on one
machine and nowhere else (CI, a fresh clone, a packaged build). A committed
tarball installs everywhere, and `pnpm pack` applies the package's
`publishConfig`, so these bytes are what `npm publish` would upload.

### The one human step

npm trusted publishing can publish a package that already EXISTS; it cannot
create a new name, and the local token is expired. Arman opens
<https://www.npmjs.com/settings/ai-matrx/packages> and makes
`@ai-matrx/content-ir-react` publishable by `AI-Matrix-Engine/aidream`
(workflow `publish-npm-package.yml`), then:

```bash
gh workflow run publish-npm-package.yml -R AI-Matrix-Engine/aidream -f tag=npm/content-ir-react/v0.1.0
```

and here:

```bash
pnpm remove @ai-matrx/content-ir-react && pnpm add @ai-matrx/content-ir-react@0.1.0
git rm desktop/vendor/ai-matrx-content-ir-react-0.1.0.tgz
```

Regenerate (after bumping the package version):

```bash
cd ../aidream/apps/shared/content-ir-react && pnpm pack --pack-destination ../../../../matrx-local/desktop/vendor
```

Cross-repo record: `common-docs/systems/content-ir-twin/FEATURE.md`.
