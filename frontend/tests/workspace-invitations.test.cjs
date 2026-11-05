const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const { randomBytes } = require('node:crypto');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, filename);
const { invitationToken } = require('../src/lib/invitation-token.ts');
const { createWorkspaceServices } = require('../src/lib/workspace-services.ts');
const originalFetch = global.fetch, originalBase = process.env.NEXT_PUBLIC_API_BASE_URL;
afterEach(() => { global.fetch = originalFetch; if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL; else process.env.NEXT_PUBLIC_API_BASE_URL = originalBase; });

test('invitation input accepts raw code, copied URL and previously encoded URL without changing case', () => {
  const token = randomBytes(32).toString('base64url');
  const link = `http://localhost:3000/invitations/${token}`;
  for (const input of [token, `  ${token}  `, link, ` ${link} `, encodeURIComponent(link), encodeURIComponent(encodeURIComponent(link)), `${link}/?from=copy#preview`]) {
    assert.equal(invitationToken(input), token);
  }
});

test('unrelated URLs and malformed input never extract a valid invitation code or throw', () => {
  for (const input of ['https://example.test/other/code', 'javascript:alert(1)', '%invalid', 'https://example.test/invitations/a/extra']) assert.equal(invitationToken(input), input);
});

test('preview, accept and decline send only the extracted token and retain bearer authorization', async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = 'http://backend.example';
  const token = randomBytes(32).toString('base64url');
  const link = `http://localhost:3000/invitations/${token}`;
  const requests = [];
  global.fetch = async (url, options) => { requests.push([url, options.method, options.headers.Authorization]); return Response.json({ data: {} }); };
  const api = createWorkspaceServices({ getToken: () => 'fixture-access', refresh: async () => 'refreshed', clear() {} });
  await api.preview(encodeURIComponent(link)); await api.accept(link); await api.decline(token);
  assert.deepEqual(requests, [
    [`http://backend.example/api/v1/workspace-invitations/${token}`, 'GET', 'Bearer fixture-access'],
    [`http://backend.example/api/v1/workspace-invitations/${token}/accept`, 'POST', 'Bearer fixture-access'],
    [`http://backend.example/api/v1/workspace-invitations/${token}/decline`, 'POST', 'Bearer fixture-access'],
  ]);
});
