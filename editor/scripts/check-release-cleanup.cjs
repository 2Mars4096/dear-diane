// Real Electron regression: cleanup must treat app.asar as a physical file.
const {app}=require('electron');
const fs=require('original-fs').promises;
const path=require('node:path');
const os=require('node:os');
const assert=require('node:assert/strict');
const exec=require('node:util').promisify(require('node:child_process').execFile);
const {createPackage}=require('@electron/asar');
app.whenReady().then(async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'diane-release-cleanup-'));
 try {
  const input=path.join(root,'input'),bundle=path.join(root,'Dear Diane.app');
  await fs.mkdir(input);await fs.writeFile(path.join(input,'test.txt'),'archive content');
  await fs.mkdir(path.join(bundle,'Contents/Resources'),{recursive:true});
  await createPackage(input,path.join(bundle,'Contents/Resources/app.asar'));
  const zip=path.join(root,'update.zip');
  await exec('/usr/bin/ditto',['-c','-k','--keepParent',bundle,zip]);
  const local=require('../dist-electron/localUpdate.js');
  local.inspectBundle=async()=>({version:'0.1.5'});
  local.stageBundle=async()=>({staged:'verified'});
  const {stagePublishedMacBundle}=require('../dist-electron/releaseBundle.js');
  assert.deepEqual(await stagePublishedMacBundle(zip,'/Applications/Dear Diane.app',root,'0.1.5'),{staged:'verified'});
  assert.equal((await fs.readdir(root)).some(n=>n.startsWith('release-')),false);
  await assert.rejects(stagePublishedMacBundle(zip,'/Applications/Dear Diane.app',root,'0.1.4'),/does not match/);
  assert.equal((await fs.readdir(root)).some(n=>n.startsWith('release-')),false);
  console.log('Real Electron ASAR cleanup passes on success and validation failure.');
 }finally{await fs.rm(root,{recursive:true,force:true});}
}).then(()=>app.exit(0),e=>{console.error(e);app.exit(1);});
