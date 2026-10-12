'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

const { main, requestBody, defaultName, apiBase, HELP, USER_AGENT } = require('../lib/cli.js');

const KEY = 'lss_test_SECRETSECRETSECRET';
const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452', 'hex');
const sha = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');

/** A fake API server: it records what it receives and answers what the test tells it. */
async function fakeApi(handler) {
    const seen = [];
    const server = http.createServer((req, res) => {
        let body = '';
        req.on('data', (chunk) => (body += chunk));
        req.on('end', () => {
            const entry = { method: req.method, url: req.url, headers: req.headers, body: body ? JSON.parse(body) : null };
            seen.push(entry);
            const answer = handler(entry, seen) || { status: 404, json: { error: { code: 'not_found', message: 'Not found.' } } };
            res.writeHead(answer.status, { 'Content-Type': answer.raw ? 'image/png' : 'application/json', ...(answer.headers || {}) });
            res.end(answer.raw ?? JSON.stringify(answer.json ?? {}));
        });
    });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    return { origin, seen, close: () => new Promise((resolve) => server.close(resolve)) };
}

function shot(origin, extra = {}) {
    return {
        id: '01TESTSHOT', status: 'succeeded',
        image: { url: `${origin}/files/shot.png?signature=abc`, mime: 'image/png', bytes: PNG.length, width: 1440, height: 900, sha256: sha(PNG) },
        country: null, credits: { charged: 1, reserved: 1, remaining: 49 }, error: null,
        ...extra,
    };
}

async function run(argv, api, env = {}, more = {}) {
    const out = [];
    const err = [];
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ls-cli-'));
    const cwd = process.cwd();
    process.chdir(dir);
    try {
        const code = await main(argv, {
            env: { LOCALSCREENSHOT_API_URL: api ? `${api.origin}/v1` : undefined, LOCALSCREENSHOT_API_KEY: KEY, HOME: dir, XDG_CONFIG_HOME: path.join(dir, 'cfg'), ...env },
            out: (text) => out.push(text), err: (text) => err.push(text),
            now: () => new Date('2026-10-12T09:08:07Z'), pollEvery: 5, ...more,
        });
        return { code, out: out.join('\n'), err: err.join('\n'), dir, files: fs.readdirSync(dir).filter((name) => name !== 'cfg') };
    } finally {
        process.chdir(cwd);
    }
}

test('options map to the real API parameters', () => {
    assert.deepEqual(requestBody('https://example.com', {}), { url: 'https://example.com', response: 'json' });
    assert.deepEqual(
        requestBody('https://example.com', { country: 'br', 'full-page': true, device: 'iphone_15', format: 'jpeg', 'click-text': 'Product', 'no-cookie-banners': true, 'no-ads': true, dark: true }),
        { url: 'https://example.com', response: 'json', country: 'BR', full_page: true, device: 'iphone_15', format: 'jpeg', click_text: 'Product', block_cookie_banners: true, block_ads: true, color_scheme: 'dark' },
    );
    assert.equal(requestBody('https://example.com', { 'block-ads': true }).block_ads, true);
    assert.throws(() => requestBody('https://example.com', { country: 'Brazil' }), /two-letter/);
    assert.throws(() => requestBody('https://example.com', { format: 'gif' }), /png, jpeg, webp or pdf/);
});

test('the default file name comes from the page host and the time, never from the server', () => {
    const now = new Date('2026-10-12T09:08:07Z');
    assert.equal(defaultName('https://www.example.com/a/b?c=1', 'image/png', now), 'example.com-20261012-090807.png');
    assert.equal(defaultName('https://example.com', 'image/jpeg', now), 'example.com-20261012-090807.jpg');
    assert.equal(defaultName('https://example.com', 'application/pdf', now), 'example.com-20261012-090807.pdf');
});

test('the API address must be https, except on this machine', () => {
    assert.equal(apiBase({}), 'https://api.localscreenshot.com/v1');
    assert.equal(apiBase({ LOCALSCREENSHOT_API_URL: 'http://127.0.0.1:9/v1/' }), 'http://127.0.0.1:9/v1');
    assert.throws(() => apiBase({ LOCALSCREENSHOT_API_URL: 'http://example.com/v1' }), /https/);
    assert.throws(() => apiBase({ LOCALSCREENSHOT_API_URL: 'https://user:pass@example.com/v1' }), /plain address/);
});

test('a screenshot is requested once, downloaded without the key, and written', async () => {
    const api = await fakeApi((req) => {
        if (req.method === 'POST' && req.url === '/v1/screenshot') {
            return { status: 200, json: shot(api.origin, { country: { requested: 'BR', observed: 'BR' } }) };
        }
        if (req.url.startsWith('/files/')) {
            return { status: 200, raw: PNG };
        }
    });
    const result = await run(['https://example.com', '--country', 'BR'], api);
    await api.close();
    assert.equal(result.code, 0, result.err);
    assert.deepEqual(result.files, ['example.com-20261012-090807.png']);
    assert.deepEqual(fs.readFileSync(path.join(result.dir, result.files[0])), PNG);
    assert.match(result.out, /1440 x 900/);
    assert.match(result.out, /asked BR, loaded from BR/);
    assert.match(result.out, /Credits: 1 used, 49 left/);

    const [post, image] = api.seen;
    assert.equal(post.headers.authorization, `Bearer ${KEY}`);
    assert.equal(post.headers['user-agent'], USER_AGENT);
    assert.match(post.headers['idempotency-key'], /^[0-9a-f-]{36}$/);
    assert.deepEqual(post.body, { url: 'https://example.com', response: 'json', country: 'BR' });
    // The signed link is read without the key.
    assert.equal(image.headers.authorization, undefined);
    assert.equal(api.seen.length, 2);
});

test('an existing file is never overwritten without --force', async () => {
    const api = await fakeApi((req) => (req.method === 'POST' ? { status: 200, json: shot(api.origin) } : { status: 200, raw: PNG }));
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ls-keep-'));
    const file = path.join(dir, 'home.png');
    fs.writeFileSync(file, 'mine');
    const kept = await run(['https://example.com', '-o', file], api);
    assert.equal(kept.code, 2);
    assert.match(kept.err, /already exists/);
    assert.equal(fs.readFileSync(file, 'utf8'), 'mine');
    // A dangling symbolic link is "already there" too: nothing is written through it.
    if (process.platform !== 'win32') {
        const link = path.join(dir, 'link.png');
        fs.symlinkSync(path.join(dir, 'elsewhere.png'), link);
        assert.equal((await run(['https://example.com', '-o', link], api)).code, 2);
        assert.ok(!fs.existsSync(path.join(dir, 'elsewhere.png')));
        fs.unlinkSync(link);
    }
    const forced = await run(['https://example.com', '-o', file, '--force'], api);
    await api.close();
    assert.equal(forced.code, 0, forced.err);
    assert.deepEqual(fs.readFileSync(file), PNG);
    assert.deepEqual(fs.readdirSync(dir), ['home.png']);
});

test('an API error prints its code, exits 1, and the key never appears', async () => {
    const api = await fakeApi(() => ({ status: 402, json: { error: { code: 'insufficient_credits', message: `Not enough credits for key ${KEY}.` } } }));
    const result = await run(['https://example.com'], api);
    assert.equal(result.code, 1);
    assert.match(result.err, /Error insufficient_credits: Not enough credits/);
    assert.ok(!(result.err + result.out).includes(KEY));
    assert.match(result.err, /\[key\]/);
    assert.deepEqual(result.files, []);

    const limited = await fakeApi(() => ({ status: 429, headers: { 'Retry-After': '7' }, json: { error: { code: 'rate_limited', message: 'Too many requests.' } } }));
    const slow = await run(['https://example.com'], limited);
    await api.close();
    await limited.close();
    assert.match(slow.err, /Error rate_limited: Too many requests\. Try again in 7 s\./);
    // Nothing is retried automatically.
    assert.equal(limited.seen.length, 1);
});

test('a screenshot that is not finished is followed by id, and a failed one says why', async () => {
    const api = await fakeApi((req, seen) => {
        if (req.method === 'POST') {
            return { status: 202, json: { id: '01PENDING', status: 'running', links: { self: 'https://evil.example/steal' } } };
        }
        if (req.url === '/v1/screenshots/01PENDING') {
            const polls = seen.filter((entry) => entry.url === '/v1/screenshots/01PENDING').length;
            return { status: 200, json: polls < 2 ? { id: '01PENDING', status: 'running' } : { id: '01PENDING', status: 'failed', error: { code: 'click_target_invalid', message: 'No element shows that text.' }, credits: { charged: 0 } } };
        }
    });
    const result = await run(['https://example.com', '--click-text', 'Nope'], api);
    await api.close();
    assert.equal(result.code, 1);
    assert.match(result.err, /Error click_target_invalid: No element shows that text\./);
    // The follow-up goes to the address built here, with the key, never to the one the server suggests.
    const polls = api.seen.filter((entry) => entry.method === 'GET');
    assert.equal(polls.length, 2);
    assert.ok(polls.every((entry) => entry.url === '/v1/screenshots/01PENDING' && entry.headers.authorization === `Bearer ${KEY}`));
});

test('a redirect, a wrong size, a wrong fingerprint or an unknown file type is refused', async () => {
    const redirect = await fakeApi(() => ({ status: 302, headers: { Location: 'https://evil.example/' } }));
    assert.match((await run(['https://example.com'], redirect)).err, /unexpected_redirect/);
    await redirect.close();

    for (const [image, expected] of [
        [{ bytes: 5 }, /image_mismatch/], [{ sha256: 'a'.repeat(64) }, /image_mismatch/], [{ mime: 'text/html' }, /bad_answer/],
    ]) {
        const api = await fakeApi((req) => {
            const base = shot(api.origin);
            return req.method === 'POST' ? { status: 200, json: { ...base, image: { ...base.image, ...image } } } : { status: 200, raw: PNG };
        });
        const result = await run(['https://example.com'], api);
        await api.close();
        assert.equal(result.code, 1);
        assert.match(result.err, expected);
        assert.deepEqual(result.files, []);
    }

    const moved = await fakeApi((req) => (req.method === 'POST' ? { status: 200, json: shot(moved.origin) } : { status: 302, headers: { Location: 'https://evil.example/x.png' } }));
    assert.match((await run(['https://example.com'], moved)).err, /image_unavailable/);
    await moved.close();
});

test('--json prints the final document and still writes the file', async () => {
    const api = await fakeApi((req) => (req.method === 'POST' ? { status: 200, json: shot(api.origin) } : { status: 200, raw: PNG }));
    const result = await run(['https://example.com', '--json'], api);
    await api.close();
    const printed = JSON.parse(result.out);
    assert.equal(printed.status, 'succeeded');
    assert.equal(path.basename(printed.file), 'example.com-20261012-090807.png');
    assert.ok(!result.out.includes(KEY));
    assert.equal(result.files.length, 1);
});

test('countries needs no key, credits does', async () => {
    const api = await fakeApi((req) => {
        if (req.url === '/v1/countries') {
            return { status: 200, json: { count: 2, countries: [{ code: 'BR', name: 'Brazil' }, { code: 'DE', name: 'Germany' }] } };
        }
        if (req.url === '/v1/credits') {
            return { status: 200, json: { credits: { remaining: 42 }, limits: { requests_per_minute: 10, concurrent_screenshots: 2 } } };
        }
    });
    const list = await run(['countries'], api, { LOCALSCREENSHOT_API_KEY: '' });
    assert.equal(list.code, 0, list.err);
    assert.equal(list.out, 'BR  Brazil\nDE  Germany');
    assert.equal(api.seen[0].headers.authorization, undefined);

    const balance = await run(['credits'], api);
    assert.match(balance.out, /42 credits left\n10 requests a minute, 2 screenshots at a time/);

    const none = await run(['credits'], api, { LOCALSCREENSHOT_API_KEY: '' });
    await api.close();
    assert.equal(none.code, 2);
    assert.match(none.err, /No API key/);
});

test('login checks the key, saves it for the owner only, and logout removes it', async () => {
    const api = await fakeApi((req) => (req.headers.authorization === `Bearer ${KEY}` ? { status: 200, json: { credits: { remaining: 50 } } } : { status: 401, json: { error: { code: 'invalid_api_key', message: `The key ${String(req.headers.authorization).slice(7)} is not valid.` } } }));
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ls-home-'));
    const env = { LOCALSCREENSHOT_API_KEY: '', HOME: dir, XDG_CONFIG_HOME: path.join(dir, 'cfg') };
    const file = path.join(dir, 'cfg', 'localscreenshot', 'config.json');

    const wrong = await run(['login'], api, env, { readSecret: async () => 'lss_test_WRONGWRONGWRONG' });
    assert.equal(wrong.code, 1);
    // The API echoes the refused key in its message: it is masked all the same.
    assert.ok(!wrong.err.includes('lss_test_WRONGWRONGWRONG'));
    assert.deepEqual(fs.existsSync(path.dirname(file)) ? fs.readdirSync(path.dirname(file)) : [], []);
    assert.match(wrong.err, /invalid_api_key/);
    assert.ok(!fs.existsSync(file));

    const saved = await run(['login'], api, env, { readSecret: async () => ` ${KEY}\n` });
    assert.equal(saved.code, 0, saved.err);
    assert.ok(!(saved.out + saved.err).includes(KEY));
    assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).api_key, KEY);
    if (process.platform !== 'win32') {
        assert.equal(fs.statSync(file).mode & 0o777, 0o600);
        assert.equal(fs.statSync(path.dirname(file)).mode & 0o777, 0o700);
    }

    // The saved key is then used without the environment variable.
    const balance = await run(['credits'], api, env);
    assert.match(balance.out, /50 credits left/);

    const out = await run(['logout'], api, env);
    await api.close();
    assert.match(out.out, /Key removed/);
    assert.ok(!fs.existsSync(file));
});

test('usage errors exit 2 and the help never shows a key', async () => {
    assert.equal((await run([], null)).code, 2);
    assert.equal((await run(['--help'], null)).code, 0);
    assert.ok(!HELP.includes(KEY) && !/Bearer/.test(HELP));
    assert.match((await run(['example.com'], null)).err, /starts with https:\/\//);
    // A key typed where the address goes is not echoed back.
    assert.ok(!(await run([KEY], null, { LOCALSCREENSHOT_API_KEY: '' })).err.includes(KEY));
    assert.match((await run(['https://a.com', 'https://b.com'], null)).err, /One page at a time/);
    assert.match((await run(['https://a.com', '--api-key', KEY], null)).err, /--help/);
    assert.equal((await run(['--version'], null)).out, require('../package.json').version);
});
