# localscreenshot CLI

Take a screenshot of any public web page from your terminal, from Node or from Python. This is the command line client of the [localscreenshot](https://localscreenshot.com) API.

```bash
npx localscreenshot https://example.com
```

```bash
pipx run localscreenshot https://example.com
```

Both packages offer the same command, with the same options. Neither has a dependency.

| Package | Folder | Install |
| --- | --- | --- |
| [`localscreenshot` on npm](https://www.npmjs.com/package/localscreenshot) | [`npm/`](npm/) | `npm install -g localscreenshot` (Node 18 or later) |
| [`localscreenshot` on PyPI](https://pypi.org/project/localscreenshot/) | [`python/`](python/) | `pip install localscreenshot` (Python 3.9 or later) |

## Quick start

1. Create a free account at [localscreenshot.com/signup](https://localscreenshot.com/signup). A new account gets free credits, no card needed.
2. Save your API key once: `localscreenshot login`, or set `LOCALSCREENSHOT_API_KEY`.
3. Take a screenshot:

```bash
localscreenshot https://example.com --full-page
localscreenshot https://www.netflix.com --country BR
localscreenshot https://example.com --device iphone_15 --dark --no-cookie-banners
localscreenshot https://www.notion.com --click-text "Product"
```

The options are described in the README of each package. The Python package also exposes a function: `from localscreenshot import screenshot`.

## What the client does with your key

- It reads the key from `LOCALSCREENSHOT_API_KEY`, or from a file that `localscreenshot login` writes for your user only.
- It sends the key to the API host only (`api.localscreenshot.com`, or the address you set yourself in `LOCALSCREENSHOT_API_URL`), over HTTPS. Redirects are not followed.
- It never prints the key and never accepts it as a command line argument.
- It sends no telemetry and does not check for updates.

## Development

```bash
cd npm && npm test                                # Node's built-in test runner, no network
cd python && python -m unittest discover -s tests # standard library only, no network
```

The tests run against a fake API started on your machine.

## Links

- [Documentation](https://docs.localscreenshot.com)
- [API](https://localscreenshot.com/api)
- [MCP server for AI tools](https://localscreenshot.com/mcp)
- [Pricing](https://localscreenshot.com/pricing)

MIT license.
