// Record the build location so installed development copies can find the next update.
const fs = require('node:fs');
const path = require('node:path');
fs.writeFileSync(path.resolve('dist-electron/local-build.json'), JSON.stringify({path:path.resolve('release',`mac-${process.arch}`,'Dear Diane.app')}));
