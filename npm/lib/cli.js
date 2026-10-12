'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { parseArgs } = require('node:util');

const VERSION = require('../package.json').version;
const DEFAULT_API = 'https://api.localscreenshot.com/v1';
const KEY_VARIABLE = 'LOCALSCREENSHOT_API_KEY';
const USER_AGENT = `localscreenshot-cli/${VERSION} (npm)`;
const TIMEOUT_MS = 120_000;
const MAX_IMAGE_BYTES = 50 * 1024 * 1024;
const POLL_EVERY_MS = 2_000;
const TOTAL_MS = 180_000;
const EXTENSIONS = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'application/pdf': 'pdf' };

const HELP = `localscreenshot ${VERSION}
Take a screenshot of a public web page.

Usage
  localscreenshot <url> [options]
  localscreenshot countries        list the countries a page can be loaded from
  localscreenshot credits          show the credits left on your account
  localscreenshot login            save your API key on this machine
  localscreenshot logout           remove the saved key

Options
  -o, --output <file>      where to write the image (default: <host>-<time>.<ext>)
      --country <code>     load the page from this country, two letters (BR, DE, JP)
      --full-page          capture the whole page, not only the first screen
      --device <name>      screen to render on (iphone_15, ipad, desktop_hd, ...)
      --format <format>    png (default), jpeg, webp or pdf
      --click-text <text>  click the one element showing this text, then capture
      --no-cookie-banners  hide cookie banners
      --no-ads             hide ads
      --dark               render in dark mode
      --json               print the API answer as JSON
      --force              overwrite the output file if it exists
  -h, --help               show this help
  -v, --version            show the version

Your API key
  Set ${KEY_VARIABLE}, or run "localscreenshot login".
  Create a free account at https://localscreenshot.com/signup

Docs: https://docs.localscreenshot.com
`;

class UsageError extends Error {}
class ApiError extends Error {
    constructor(code, message, status) {
        super(message);
        this.code = code;
        this.status = status;
    }
}

/** The API address. It can be overridden for tests; outside this machine it must be https, since the key travels to it. */
function apiBase(env) {
    const raw = (env.LOCALSCREENSHOT_API_URL || DEFAULT_API).replace(/\/+$/, '');
    let url;
    try {
        url = new URL(raw);
    } catch {
        throw new UsageError('LOCALSCREENSHOT_API_URL is not a valid address.');
    }
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) {
        throw new UsageError('LOCALSCREENSHOT_API_URL must use https.');
    }
    if (url.username || url.password || url.search || url.hash) {
        throw new UsageError('LOCALSCREENSHOT_API_URL must be a plain address.');
    }
    return raw;
}

function configFile(env) {
    if (process.platform === 'win32' && env.APPDATA) {
        return path.join(env.APPDATA, 'localscreenshot', 'config.json');
    }
    const base = env.XDG_CONFIG_HOME || path.join(env.HOME || os.homedir(), '.config');
    return path.join(base, 'localscreenshot', 'config.json');
}

function looksLikeKey(value) {
    return typeof value === 'string' && /^[\x21-\x7e]{8,200}$/.test(value);
}

function readKey(env) {
    const fromEnv = (env[KEY_VARIABLE] || '').trim();
    if (fromEnv !== '') {
        if (!looksLikeKey(fromEnv)) {
            throw new UsageError(`${KEY_VARIABLE} does not look like an API key.`);
        }
        return fromEnv;
    }
    try {
        const saved = JSON.parse(fs.readFileSync(configFile(env), 'utf8')).api_key;
        if (looksLikeKey(saved)) {
            return saved;
        }
    } catch {
        // No file, or unreadable: treated as "no key".
    }
    throw new UsageError(`No API key. Set ${KEY_VARIABLE} or run "localscreenshot login".\nCreate a free account at https://localscreenshot.com/signup`);
}

function saveKey(env, key) {
    const file = configFile(env);
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    const temp = `${file}.${crypto.randomBytes(6).toString('hex')}.tmp`;
    // Created with mode 0600 from the start: the key is never readable by another account, not even briefly.
    try {
        fs.writeFileSync(temp, JSON.stringify({ api_key: key }) + '\n', { mode: 0o600, flag: 'wx' });
        fs.renameSync(temp, file);
    } catch (error) {
        // The key must not stay anywhere but in the announced file.
        fs.rmSync(temp, { force: true });
        throw error;
    }
    try {
        fs.chmodSync(file, 0o600);
    } catch {
        // Windows: permissions there are those of the user's folder.
    }
    return file;
}

/** Never let the key out in a message, whatever the server answers. */
function scrub(text, keys) {
    let clean = String(text);
    for (const key of keys) {
        if (key) {
            clean = clean.split(key).join('[key]');
        }
    }
    return clean;
}

async function call(io, method, url, { key, body, idempotencyKey, timeout = TIMEOUT_MS } = {}) {
    const headers = { Accept: 'application/json', 'User-Agent': USER_AGENT };
    if (key) {
        headers.Authorization = `Bearer ${key}`;
    }
    if (body !== undefined) {
        headers['Content-Type'] = 'application/json';
    }
    if (idempotencyKey) {
        headers['Idempotency-Key'] = idempotencyKey;
    }
    let response;
    try {
        // A redirect is never followed when the request carries the key: it only goes to the chosen host.
        response = await io.fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), redirect: 'manual', signal: AbortSignal.timeout(Math.max(1, Math.floor(timeout))) });
    } catch (error) {
        throw new ApiError('network_error', error && error.name === 'TimeoutError' ? 'The request timed out.' : 'Could not reach the API.', 0);
    }
    if (response.status >= 300 && response.status < 400) {
        throw new ApiError('unexpected_redirect', 'The API answered with a redirect, which this tool does not follow.', response.status);
    }
    let data = null;
    try {
        data = await response.json();
    } catch {
        // Empty or non-JSON body: the HTTP status is enough to say what happened.
    }
    if (response.status >= 400) {
        const error = (data && data.error) || {};
        // Nothing is retried automatically: a screenshot costs credits, so retrying is the user's call.
        const wait = Number(response.headers.get('retry-after'));
        const hint = response.status === 429 && Number.isFinite(wait) && wait > 0 ? ` Try again in ${Math.ceil(wait)} s.` : '';
        throw new ApiError(error.code || `http_${response.status}`, (error.message || `The API answered ${response.status}.`) + hint, response.status);
    }
    return { status: response.status, data: data || {} };
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function download(io, url, image, timeout) {
    let target;
    try {
        target = new URL(url);
    } catch {
        throw new ApiError('bad_image_link', 'The API returned an image link this tool cannot read.', 0);
    }
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(target.hostname);
    if (target.protocol !== 'https:' && !(target.protocol === 'http:' && local)) {
        throw new ApiError('bad_image_link', 'The API returned an image link that is not https.', 0);
    }
    let response;
    try {
        // Signed link: it is read WITHOUT the key, which therefore never leaves the API host.
        response = await io.fetch(target, { headers: { 'User-Agent': USER_AGENT }, redirect: 'manual', signal: AbortSignal.timeout(Math.max(1, Math.floor(timeout))) });
    } catch {
        throw new ApiError('network_error', 'Could not download the image.', 0);
    }
    // No redirect is followed: the signed link is read where the API points, nowhere else.
    if (!response.ok) {
        throw new ApiError('image_unavailable', `The image could not be downloaded (${response.status}).`, response.status);
    }
    const declared = Number(response.headers.get('content-length') || 0);
    if (declared > MAX_IMAGE_BYTES) {
        throw new ApiError('image_too_large', 'The image is larger than this tool accepts.', 0);
    }
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
        size += chunk.length;
        if (size > MAX_IMAGE_BYTES) {
            throw new ApiError('image_too_large', 'The image is larger than this tool accepts.', 0);
        }
        chunks.push(chunk);
    }
    const bytes = Buffer.concat(chunks);
    // The file written is the one the API describes: same size, same fingerprint.
    if (Number.isInteger(image.bytes) && image.bytes !== bytes.length) {
        throw new ApiError('image_mismatch', 'The downloaded image does not have the size the API announced.', 0);
    }
    if (typeof image.sha256 === 'string' && image.sha256 !== '' && crypto.createHash('sha256').update(bytes).digest('hex') !== image.sha256.toLowerCase()) {
        throw new ApiError('image_mismatch', 'The downloaded image does not match the fingerprint the API announced.', 0);
    }
    return bytes;
}

function defaultName(pageUrl, mime, now) {
    let host = 'screenshot';
    try {
        host = new URL(pageUrl).hostname.replace(/^www\./, '').replace(/[^a-z0-9.-]/gi, '').replace(/^\.+/, '') || 'screenshot';
    } catch {
        // Address without a scheme: the default name stays "screenshot".
    }
    const stamp = now.toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
    return `${host.slice(0, 80)}-${stamp}.${EXTENSIONS[mime]}`;
}

function writeImage(file, bytes, force) {
    const target = path.resolve(file);
    const refusal = 'The output file already exists. Use --force to overwrite it, or -o to choose another name.';
    // lstat, not exists: a dangling symbolic link counts as "already there".
    if (!force && fs.lstatSync(target, { throwIfNoEntry: false })) {
        throw new UsageError(refusal);
    }
    const temp = `${target}.${crypto.randomBytes(6).toString('hex')}.tmp`;
    try {
        fs.writeFileSync(temp, bytes, { flag: 'wx' });
        if (force) {
            fs.renameSync(temp, target);
        } else {
            // Exclusive copy, then removal: fails if the file appeared in the meantime, instead of overwriting it.
            fs.copyFileSync(temp, target, fs.constants.COPYFILE_EXCL);
            fs.unlinkSync(temp);
        }
    } catch (error) {
        fs.rmSync(temp, { force: true });
        if (error && error.code === 'EEXIST') {
            throw new UsageError(refusal);
        }
        throw error;
    }
    return target;
}

function requestBody(url, values) {
    const body = { url, response: 'json' };
    if (values.country !== undefined) {
        if (!/^[A-Za-z]{2}$/.test(values.country)) {
            throw new UsageError('--country takes a two-letter country code, for example BR.');
        }
        body.country = values.country.toUpperCase();
    }
    if (values['full-page']) {
        body.full_page = true;
    }
    if (values.device !== undefined) {
        body.device = values.device;
    }
    if (values.format !== undefined) {
        if (!['png', 'jpeg', 'webp', 'pdf'].includes(values.format)) {
            throw new UsageError('--format is png, jpeg, webp or pdf.');
        }
        body.format = values.format;
    }
    if (values['click-text'] !== undefined) {
        body.click_text = values['click-text'];
    }
    if (values['no-cookie-banners'] || values['block-cookie-banners']) {
        body.block_cookie_banners = true;
    }
    if (values['no-ads'] || values['block-ads']) {
        body.block_ads = true;
    }
    if (values.dark) {
        body.color_scheme = 'dark';
    }
    return body;
}

async function waitFor(io, base, key, shot, left) {
    let current = shot;
    // The follow-up address is built here from the id: never an address returned by the server.
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(String(current.id || ''))) {
        throw new ApiError('bad_answer', 'The API answered without a screenshot id.', 0);
    }
    while (!['succeeded', 'failed'].includes(current.status)) {
        if (left() <= (io.pollEvery ?? POLL_EVERY_MS)) {
            throw new ApiError('still_running', `The screenshot is still being taken (id ${current.id}). It will appear in your dashboard.`, 0);
        }
        await sleep(io.pollEvery ?? POLL_EVERY_MS);
        current = (await call(io, 'GET', `${base}/screenshots/${current.id}`, { key, timeout: left() })).data;
    }
    return current;
}

async function takeScreenshot(io, url, values) {
    const base = apiBase(io.env);
    const body = requestBody(url, values);
    const key = readKey(io.env);
    // One deadline for the whole operation (request, wait, download), on a clock that never goes backwards.
    const started = performance.now();
    const left = () => (io.totalMs ?? TOTAL_MS) - (performance.now() - started);
    const first = await call(io, 'POST', `${base}/screenshot`, { key, body, idempotencyKey: crypto.randomUUID(), timeout: Math.min(TIMEOUT_MS, left()) });
    const shot = await waitFor(io, base, key, first.data, left);
    if (shot.status !== 'succeeded' || !shot.image || !shot.image.url) {
        const error = shot.error || {};
        throw new ApiError(error.code || 'failed', error.message || 'The screenshot failed.', 0);
    }
    if (!EXTENSIONS[shot.image.mime]) {
        throw new ApiError('bad_answer', 'The API returned a file type this tool does not know.', 0);
    }
    const bytes = await download(io, shot.image.url, shot.image, Math.min(TIMEOUT_MS, left()));
    const file = writeImage(values.output || defaultName(url, shot.image.mime, io.now()), bytes, Boolean(values.force));
    if (values.json) {
        io.out(JSON.stringify({ ...shot, file }, null, 2));
        return;
    }
    const lines = [`Saved ${file}`, `${shot.image.width} x ${shot.image.height}, ${bytes.length.toLocaleString('en-US')} bytes`];
    if (shot.country) {
        lines.push(`Country: asked ${shot.country.requested}, loaded from ${shot.country.observed ?? 'unknown'}`);
    }
    const credits = shot.credits || {};
    if (credits.charged !== null && credits.charged !== undefined) {
        lines.push(`Credits: ${credits.charged} used${credits.remaining === null || credits.remaining === undefined ? '' : `, ${credits.remaining} left`}`);
    }
    io.out(lines.join('\n'));
}

async function countries(io, values) {
    const { data } = await call(io, 'GET', `${apiBase(io.env)}/countries`);
    if (values.json) {
        io.out(JSON.stringify(data, null, 2));
        return;
    }
    io.out((data.countries || []).map((country) => `${country.code}  ${country.name}`).join('\n'));
}

async function credits(io, values) {
    const key = readKey(io.env);
    const { data } = await call(io, 'GET', `${apiBase(io.env)}/credits`, { key });
    if (values.json) {
        io.out(JSON.stringify(data, null, 2));
        return;
    }
    const limits = data.limits || {};
    io.out(`${data.credits ? data.credits.remaining : 'unknown'} credits left\n${limits.requests_per_minute} requests a minute, ${limits.concurrent_screenshots} screenshots at a time`);
}

async function login(io) {
    const key = (await io.readSecret('API key: ')).trim();
    // Known from now on, so that no later message can show it, even if the API echoes it back.
    io.secrets.add(key);
    if (!looksLikeKey(key)) {
        throw new UsageError('That does not look like an API key. Copy it from your dashboard: https://localscreenshot.com/dashboard');
    }
    // Checked before it is written: a key that does not work is not saved.
    const { data } = await call(io, 'GET', `${apiBase(io.env)}/credits`, { key });
    const file = saveKey(io.env, key);
    io.out(`Key saved in ${file}\n${data.credits ? data.credits.remaining : 'unknown'} credits left`);
}

function logout(io) {
    const file = configFile(io.env);
    if (fs.existsSync(file)) {
        fs.rmSync(file);
        io.out(`Key removed from ${file}`);
        return;
    }
    io.out('No saved key.');
}

function readSecret(prompt) {
    const stdin = process.stdin;
    if (!stdin.isTTY) {
        // Piped input (a secret manager, a file): one line, no prompt.
        return new Promise((resolve) => {
            let text = '';
            stdin.setEncoding('utf8');
            stdin.on('data', (chunk) => (text += chunk));
            stdin.on('end', () => resolve(text.split(/\r?\n/)[0] || ''));
        });
    }
    return new Promise((resolve, reject) => {
        process.stderr.write(prompt);
        stdin.setRawMode(true);
        stdin.resume();
        stdin.setEncoding('utf8');
        let text = '';
        const done = (fn, value) => {
            stdin.setRawMode(false);
            stdin.pause();
            stdin.removeListener('data', onData);
            process.stderr.write('\n');
            fn(value);
        };
        const onData = (chunk) => {
            for (const char of chunk) {
                if (char === '\r' || char === '\n' || char === '\u0004') {
                    return done(resolve, text);
                }
                if (char === '\u0003') {
                    return done(reject, new UsageError('Cancelled.'));
                }
                if (char === '\u007f' || char === '\b') {
                    text = text.slice(0, -1);
                } else {
                    text += char;
                }
            }
        };
        stdin.on('data', onData);
    });
}

function parse(argv) {
    try {
        return parseArgs({
            args: argv,
            allowPositionals: true,
            options: {
                output: { type: 'string', short: 'o' },
                country: { type: 'string' },
                'full-page': { type: 'boolean' },
                device: { type: 'string' },
                format: { type: 'string' },
                'click-text': { type: 'string' },
                'no-cookie-banners': { type: 'boolean' },
                'no-ads': { type: 'boolean' },
                'block-cookie-banners': { type: 'boolean' },
                'block-ads': { type: 'boolean' },
                dark: { type: 'boolean' },
                json: { type: 'boolean' },
                force: { type: 'boolean' },
                help: { type: 'boolean', short: 'h' },
                version: { type: 'boolean', short: 'v' },
            },
        });
    } catch {
        // The parseArgs message can echo the refused argument, and someone may have put their key there. It is not repeated.
        throw new UsageError('Unknown or incomplete option.\nRun "localscreenshot --help" to see the options.');
    }
}

/**
 * Entry point, with no hidden side effect: everything that touches the outside (network, output, clock, input)
 * goes through `io`, so that it can be tested against a fake server. Returns the exit code.
 */
async function main(argv, overrides = {}) {
    const io = {
        env: process.env,
        fetch: globalThis.fetch,
        out: (text) => process.stdout.write(text + '\n'),
        err: (text) => process.stderr.write(text + '\n'),
        now: () => new Date(),
        readSecret,
        ...overrides,
    };
    io.secrets = new Set();
    try {
        const { values, positionals } = parse(argv);
        if (values.version) {
            io.out(VERSION);
            return 0;
        }
        if (values.help || positionals.length === 0) {
            io.out(HELP);
            return values.help ? 0 : 2;
        }
        if (positionals.length > 1) {
            throw new UsageError('One page at a time: give a single address.');
        }
        try {
            io.secrets.add(readKey(io.env));
        } catch {
            // No key: it is only needed here to mask it, the command itself will say what is missing.
        }
        const [command] = positionals;
        if (command === 'countries') {
            await countries(io, values);
        } else if (command === 'credits') {
            await credits(io, values);
        } else if (command === 'login') {
            await login(io);
        } else if (command === 'logout') {
            logout(io);
        } else if (/^https?:\/\//i.test(command)) {
            await takeScreenshot(io, command, values);
        } else {
            // What was typed is not echoed: someone may have put their key there by mistake.
            throw new UsageError('That is not a command or a web address. An address starts with https://');
        }
        return 0;
    } catch (error) {
        if (error instanceof UsageError) {
            io.err(scrub(error.message, io.secrets));
            return 2;
        }
        if (error instanceof ApiError) {
            io.err(scrub(`Error ${error.code}: ${error.message}`, io.secrets));
            return 1;
        }
        io.err(scrub(`Unexpected error: ${error && error.message ? error.message : 'unknown'}`, io.secrets));
        return 1;
    }
}

module.exports = { main, requestBody, defaultName, apiBase, configFile, scrub, VERSION, USER_AGENT, HELP };
