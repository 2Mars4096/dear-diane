// Real Electron regression: plain Node does not emulate its archive filesystem.
// Run: electron scripts/check-prepared-build.cjs (after electron:compile).
const {app}=require('electron');
const fs=require('node:fs/promises');
const raw=require('original-fs');
const path=require('node:path');
const os=require('node:os');
const assert=require('node:assert/strict');
const {createPackage}=require('@electron/asar');
const {findPreparedBuild}=require(process.argv[2] || '../dist-electron/preparedBuild.js');
app.whenReady().then(async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'dan-archive-test-'));
 const previous=process.env.DAN_LOCAL_UPDATE_PATH;delete process.env.DAN_LOCAL_UPDATE_PATH;
 try {
  const input=path.join(root,'input');await fs.mkdir(input);
  const target=path.join(root,'installed.app'), candidate=path.join(root,'update.app');
  const archive=b=>path.join(b,'Contents/Resources/app.asar');
  for (const [bundle,content] of [[target,'old'],[candidate,'new']]) {
   await fs.mkdir(path.dirname(archive(bundle)),{recursive:true});
   await fs.writeFile(path.join(input,'version.txt'),content);
   await createPackage(input,archive(bundle));
  }
  assert.equal((await fs.stat(archive(target))).isDirectory(),true,'must exercise Electron virtual archive semantics');
  const marker=path.join(root,'local-build.json');await fs.writeFile(marker,JSON.stringify({path:candidate}));
  assert.equal(await findPreparedBuild(marker,target),candidate);
  raw.copyFileSync(archive(candidate),archive(target));
  assert.equal(await findPreparedBuild(marker,target),null);
  console.log('Electron archive discovery: changed and identical builds passed.');
 } finally {if(previous===undefined)delete process.env.DAN_LOCAL_UPDATE_PATH;else process.env.DAN_LOCAL_UPDATE_PATH=previous;raw.rmSync(root,{recursive:true,force:true});}
}).then(()=>app.exit(0),e=>{console.error(e);app.exit(1);});
