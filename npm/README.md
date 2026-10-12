# localscreenshot

Take a screenshot of any public web page from your terminal. This is the command line tool of the [localscreenshot](https://localscreenshot.com) API.

```bash
npx localscreenshot https://example.com
```

It saves the image in the current folder and tells you what it cost.

## Install

```bash
npm install -g localscreenshot
```

Node 18 or later. No dependencies.

## Your API key

Create a free account at [localscreenshot.com/signup](https://localscreenshot.com/signup). A new account gets free credits, no card needed.

Then either set the key in your environment:

```bash
export LOCALSCREENSHOT_API_KEY=your_key
```

or save it once on this machine:

```bash
localscreenshot login
```

`login` asks for the key without showing it, checks it, and writes it to a file only you can read. `localscreenshot logout` removes it. The key is never accepted as a command line argument, so it does not end up in your shell history.

## Examples

```bash
# The whole page, as a JPEG
localscreenshot https://example.com --full-page --format jpeg

# As seen from Brazil
localscreenshot https://www.netflix.com --country BR

# On a phone, in dark mode, without the cookie banner
localscreenshot https://example.com --device iphone_15 --dark --no-cookie-banners

# Click a menu first, then capture
localscreenshot https://www.notion.com --click-text "Product"

# Choose the file name
localscreenshot https://example.com -o home.png
```

## Options

| Option | What it does |
| --- | --- |
| `-o, --output <file>` | Where to write the image. Default: `<host>-<time>.<ext>` |
| `--country <code>` | Load the page from this country, two letters |
| `--full-page` | Capture the whole page |
| `--device <name>` | Screen to render on, for example `iphone_15`, `ipad`, `desktop_hd` |
| `--format <format>` | `png` (default), `jpeg`, `webp` or `pdf` |
| `--click-text <text>` | Click the one element showing this text, then capture |
| `--no-cookie-banners` | Hide cookie banners |
| `--no-ads` | Hide ads |
| `--dark` | Render in dark mode |
| `--json` | Print the API answer as JSON |
| `--force` | Overwrite the output file if it exists |

Other commands:

```bash
localscreenshot countries   # the countries a page can be loaded from
localscreenshot credits     # the credits left on your account
```

A screenshot uses credits from your account. A failed screenshot is not charged. The tool exits with a non-zero code when something fails, and prints the error code of the API.

## For AI tools

localscreenshot also runs an MCP server, so that Claude, Cursor, VS Code and other tools can take screenshots on their own. Setup: [localscreenshot.com/mcp](https://localscreenshot.com/mcp).

## Links

- [Documentation](https://docs.localscreenshot.com)
- [API](https://localscreenshot.com/api)
- [Pricing](https://localscreenshot.com/pricing)

MIT license.
