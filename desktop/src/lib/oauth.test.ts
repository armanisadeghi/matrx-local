import { afterEach, beforeEach, expect, it, vi } from 'vitest';

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv('VITE_SUPABASE_URL', 'https://db.matrxserver.com');
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, value); },
    removeItem: (key: string) => { store.delete(key); },
  });
});

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it('keeps the verifier out of the authorization request while sending its S256 challenge', async () => {
  const oauth = await import('./oauth');
  const first = await oauth.buildOAuthAuthorizeUrl({ redirectUri: 'aimatrx://auth/callback' });
  const second = await oauth.buildOAuthAuthorizeUrl({ redirectUri: 'aimatrx://auth/callback' });
  const url = new URL(first.url);
  expect(url.searchParams.get('state')?.includes(first.codeVerifier)).toBe(false);
  expect(first.url).not.toContain(first.codeVerifier);
  expect(url.searchParams.get('code_challenge')).toBe(await oauth.generateCodeChallenge(first.codeVerifier));
  expect(url.searchParams.get('code_challenge_method')).toBe('S256');
  expect(first.state).not.toBe(second.state);
  expect(first.codeVerifier).not.toBe(second.codeVerifier);
});

it('matches the RFC 7636 S256 example', async () => {
  const { generateCodeChallenge } = await import('./oauth');
  expect(await generateCodeChallenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'))
    .toBe('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
});

it.each([
  ['foreign-state', 'aimatrx://auth/callback'],
  ['expected-state', 'aimatrx://other/callback'],
])('refuses a callback outside the initiating transaction', async (state, redirect) => {
  const oauth = await import('./oauth');
  const send = vi.fn(); vi.stubGlobal('fetch', send);
  oauth.saveOAuthState('local-verifier', 'expected-state', 'aimatrx://auth/callback');
  await expect(oauth.exchangeOAuthCode('unused-code', state, redirect)).rejects.toThrow('does not match');
  expect(send).not.toHaveBeenCalled();
});

it('claims a matching callback once before sending only the locally retained verifier', async () => {
  const oauth = await import('./oauth');
  const send = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    access_token: 'test-access', refresh_token: 'test-refresh', expires_in: 3600, token_type: 'bearer',
  })));
  vi.stubGlobal('fetch', send);
  oauth.saveOAuthState('local-verifier', 'expected-state', 'aimatrx://auth/callback');
  const pending = oauth.exchangeOAuthCode('unused-code', 'expected-state', 'aimatrx://auth/callback');
  await expect(oauth.exchangeOAuthCode('unused-code', 'expected-state', 'aimatrx://auth/callback'))
    .rejects.toThrow('does not match');
  await pending;
  expect(send).toHaveBeenCalledTimes(1);
  const body = new URLSearchParams(send.mock.calls[0]?.[1]?.body);
  expect(body.get('code_verifier')).toBe('local-verifier');
  expect(body.get('code')).toBe('unused-code');
  expect(body.get('redirect_uri')).toBe('aimatrx://auth/callback');
});

it('does not surface untrusted token-endpoint error contents', async () => {
  const oauth = await import('./oauth');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('sensitive-provider-response', { status: 400 })));
  oauth.saveOAuthState('local-verifier', 'expected-state', 'aimatrx://auth/callback');
  await expect(oauth.exchangeOAuthCode('unused-code', 'expected-state', 'aimatrx://auth/callback'))
    .rejects.toThrow('Sign-in exchange failed (HTTP 400). Please try again.');
});


it('ignores stale callback state without consuming the pending sign-in and clears it on cancel', async () => {
  const oauth = await import('./oauth');
  oauth.saveOAuthState('local-verifier', 'current-state', 'aimatrx://auth/callback');
  expect(oauth.isCurrentOAuthCallback('stale-state', 'aimatrx://auth/callback')).toBe(false);
  expect(oauth.isCurrentOAuthCallback('current-state', 'aimatrx://auth/callback')).toBe(true);
  oauth.clearOAuthState();
  expect(oauth.isCurrentOAuthCallback('current-state', 'aimatrx://auth/callback')).toBe(false);
});

it('replaces malformed response parsing errors with fixed safe copy', async () => {
  const oauth = await import('./oauth');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('untrusted-content-not-json')));
  oauth.saveOAuthState('local-verifier', 'expected-state', 'aimatrx://auth/callback');
  await expect(oauth.exchangeOAuthCode('unused-code', 'expected-state', 'aimatrx://auth/callback'))
    .rejects.toThrow('Sign-in service returned an invalid response. Please try again.');
});
