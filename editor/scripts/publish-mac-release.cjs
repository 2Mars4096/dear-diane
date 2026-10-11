// Usage: node scripts/publish-mac-release.cjs <artifact-dir> <notes-file> <commit-sha>
// Validates local bytes, uploads a draft, verifies asset names/sizes, then publishes.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const yaml = require(require.resolve('js-yaml', { paths: [path.dirname(require.resolve('electron-updater/package.json'))] }));
const [directory, notes, commit] = process.argv.slice(2);
if (!directory || !notes || !/^[a-f0-9]{40}$/.test(commit || '')) throw Error('Provide artifact directory, release notes file and the exact published source commit SHA.');
const root = path.resolve(directory);
const manifestPath = path.join(root, 'latest-mac.yml');
const manifest = yaml.load(fs.readFileSync(manifestPath, 'utf8'));
if (!/^\d+\.\d+\.\d+$/.test(manifest.version) || !Array.isArray(manifest.files)) throw Error('Invalid stable release manifest.');
if (!manifest.files.some(file => file.url.endsWith('.zip'))) throw Error('The release needs a ZIP for in-app updates.');
if (process.env.RELEASE_TAG && process.env.RELEASE_TAG !== `v${manifest.version}`) throw Error("Release tag and manifest version differ.");
const files = [manifestPath];
for (const file of manifest.files) {
  if (path.basename(file.url) !== file.url || !/^[\w.-]+\.(zip|dmg)$/.test(file.url)) throw Error('Use safe, space-free release filenames.');
  const filename = path.join(root, file.url);
  const bytes = fs.readFileSync(filename);
  if (bytes.length !== file.size || crypto.createHash('sha512').update(bytes).digest('base64') !== file.sha512) throw Error('Release checksum or size mismatch.');
  files.push(filename);
  if (fs.existsSync(filename + '.blockmap')) files.push(filename + '.blockmap');
}
fs.accessSync(notes);
const repo = '2Mars4096/deep-agent-network';
const tag = `v${manifest.version}`;
const gh = args => execFileSync('gh', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
try {
  // Read-only auth/source checks happen before any remote mutation.
  gh(['api', `repos/${repo}/commits/${commit}`]);
  if (process.env.RELEASE_TAG) {
    const taggedCommit = gh(['api', `repos/${repo}/commits/${tag}`, '--jq', '.sha']).trim();
    if (taggedCommit !== commit) throw Error('Release tag does not match the built source commit.');
  }
  let existing;
  try { existing = JSON.parse(gh(['api', `repos/${repo}/releases/tags/${tag}`])); }
  catch (error) { if (!String(error.stderr).includes('(HTTP 404)')) throw error; }
  if (existing) {
    if (!existing.draft || existing.target_commitish !== commit) throw Error('Refusing to replace a published release or a draft from different source.');
    gh(['release', 'upload', tag, ...files, '--repo', repo, '--clobber']);
  } else {
    gh(['release', 'create', tag, ...files, '--repo', repo, '--draft', '--target', commit, '--title', `Dear Diane ${manifest.version}`, '--notes-file', path.resolve(notes)]);
  }
  const release = JSON.parse(gh(['api', `repos/${repo}/releases/tags/${tag}`]));
  for (const filename of files) {
    if (!release.assets.some(asset => asset.name === path.basename(filename) && asset.size === fs.statSync(filename).size)) throw Error('Draft upload verification failed; release remains a draft.');
  }
  gh(['release', 'edit', tag, '--repo', repo, '--draft=false', '--latest']);
  console.log(`Published https://github.com/${repo}/releases/tag/${tag}`);
} catch (error) {
  // Do not echo subprocess command/environment dumps.
  console.error(error.stderr ? 'GitHub could not complete publishing. Check authentication and repository access. Any created draft remains unpublished.' : error.message);
  process.exitCode = 1;
}
