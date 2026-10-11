const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'publish-mac-release.cjs'), 'utf8');
const commit = 'a'.repeat(40);
function exercise({ existing, tagCommit = commit, corrupt = false, missingAsset = false, tag = 'v0.1.1', apiError = '' } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'diane-publish-test-'));
  const calls = [];
  const errors = [];
  const proc = { argv: ['node', 'script', dir, path.join(dir, 'notes.md'), commit], env: { RELEASE_TAG: tag, GH_TOKEN: 'test-sensitive-token', GITHUB_REPOSITORY: '2Mars4096/dear-diane' } };
  try {
    const bytes = Buffer.from('release fixture');
    const asset = { url: 'Dear-Diane-0.1.1-mac-arm64.zip', size: bytes.length, sha512: crypto.createHash('sha512').update(bytes).digest('base64') };
    fs.writeFileSync(path.join(dir, asset.url), corrupt ? 'bad' : bytes);
    fs.writeFileSync(path.join(dir, 'latest-mac.yml'), JSON.stringify({ version: '0.1.1', files: [asset] }));
    fs.writeFileSync(path.join(dir, 'notes.md'), 'Release');
    let uploaded = false;
    const mockRequire = name => name === 'node:child_process' ? { execFileSync: (_file, args) => {
      calls.push(args);
      if (apiError) { const error = new Error("API failed"); error.stderr = apiError; throw error; }
      if (args[0] === 'api' && args[1].endsWith(`/commits/${tag}`)) return tagCommit;
      if (args[0] === 'api' && args[1].includes('/releases/tags/')) {
        if (uploaded) return JSON.stringify({ assets: missingAsset ? [] : [asset.url, 'latest-mac.yml'].map(name => ({ name, size: fs.statSync(path.join(dir, name)).size })) });
        if (existing) return JSON.stringify(existing);
        const error = new Error('Not found'); error.stderr = '(HTTP 404)'; throw error;
      }
      if (args[0] === 'release' && ['create', 'upload'].includes(args[1])) uploaded = true;
      return '{}';
    } } : require(name);
    mockRequire.resolve = require.resolve;
    let thrown;
    try { vm.runInNewContext(source, { require: mockRequire, process: proc, console: { log() {}, error: msg => errors.push(msg) } }); }
    catch (error) { thrown = error; }
    return { calls, errors, failed: !!thrown || proc.exitCode === 1 };
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}
const published = result => result.calls.some(args => args[0] === 'release' && args[1] === 'edit');
test('publishes only after uploading and verifying complete draft assets', () => {
  const result = exercise(); assert.equal(result.failed, false); assert.equal(published(result), true);
});
test('resumes a draft from the same exact source', () => {
  const result = exercise({ existing: { draft: true, target_commitish: commit } });
  assert.equal(result.failed, false); assert.ok(result.calls.some(args => args[1] === 'upload')); assert.equal(published(result), true);
});
test('refuses published releases and drafts from different source', () => {
  for (const existing of [{ draft: false, target_commitish: commit }, { draft: true, target_commitish: 'b'.repeat(40) }]) {
    const result = exercise({ existing }); assert.equal(result.failed, true); assert.equal(published(result), false);
    assert.equal(result.calls.some(args => args[0] === 'release'), false);
  }
});
test('rejects corrupt artifacts and mismatched version before contacting GitHub', () => {
  for (const options of [{ corrupt: true }, { tag: 'v0.1.2' }]) {
    const result = exercise(options); assert.equal(result.failed, true); assert.equal(result.calls.length, 0);
  }
});
test('rejects a tag pointing to different source before uploading', () => {
  const result = exercise({ tagCommit: 'b'.repeat(40) }); assert.equal(result.failed, true);
  assert.equal(result.calls.some(args => args[0] === 'release'), false);
});
test('leaves incomplete upload unpublished', () => {
  const result = exercise({ missingAsset: true }); assert.equal(result.failed, true); assert.equal(published(result), false);
});

test('reports operation and error without exposing the GitHub token', () => {
  const result = exercise({ apiError: 'HTTP 403 denied test-sensitive-token' });
  assert.equal(result.failed, true);
  assert.ok(result.errors.join(' ').includes('HTTP 403'));
  assert.ok(result.errors.join(' ').includes('GET repos/2Mars4096/dear-diane/commits/'));
  assert.equal(result.errors.join(' ').includes('test-sensitive-token'), false);
});
