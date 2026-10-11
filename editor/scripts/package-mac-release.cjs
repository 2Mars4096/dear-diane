// Sign the app before creating ZIP/DMG assets. Ad-hoc signing does not use Keychain.
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const run = (file, args, env = process.env) => execFileSync(file, args, { cwd: root, env, stdio: 'inherit' });
if (process.platform !== 'darwin' || process.arch !== 'arm64') throw new Error('Build Mac releases on macOS.');
const sdk = execFileSync('xcrun', ['--show-sdk-path'], { encoding: 'utf8' }).trim();
const env = { ...process.env, CXXFLAGS: `-isystem ${sdk}/usr/include/c++/v1`, CSC_IDENTITY_AUTO_DISCOVERY: 'false' };
run('npm', ['run', 'build:verify']);
run('npm', ['run', 'electron:compile'], { ...process.env, DAN_PUBLISHED_BUILD: '1' });
run('npx', ['--no-install', 'electron-builder', '--mac', '--dir', '--publish', 'never'], env);
const bundle = path.join(root, 'release/mac-arm64/Dear Diane.app');
run('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', bundle]);
run('/usr/bin/codesign', ['--verify', '--deep', '--strict', bundle]);
run('npx', ['--no-install', 'electron-builder', '--mac', '--prepackaged', bundle, '--publish', 'never'], env);
