# localscreenshot CLI

Command line client for [localscreenshot](https://localscreenshot.com), the screenshot API:
capture any public web page, optionally loaded from a chosen country, with cookie banners
and ads hidden.

This repository is the source of record for the command line client. It holds the code for
the two packages published from it, `localscreenshot` on npm and `localscreenshot` on PyPI.
The service itself lives at https://localscreenshot.com and its code is not part of this
repository.

## Status

The client is in development and has not been released yet. Installation instructions
(`npx localscreenshot`, `pip install localscreenshot`) will be added here at the first
release. Nothing in this repository is published until a release workflow runs and is
approved.

## How releases work

Publishing is done by GitHub Actions using trusted publishing, so no long lived registry
token exists. A tag matching `v*` triggers both workflows. Each one is attached to the
`release` environment, which requires a manual approval before it can reach the registry.
Each release carries npm provenance.

## License

MIT, see [LICENSE](LICENSE).
