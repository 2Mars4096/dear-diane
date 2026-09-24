import { expect, it } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { findPreparedBuild, updateErrorMessage } from '../../electron/preparedBuild';
it('finds changed same-version builds, skips missing and identical builds, and notices rebuilds', async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'dan-prepared-'));
 const marker=path.join(root,'local-build.json');const installed=path.join(root,'installed.app');const candidate=path.join(root,'new.app');
 const asar=(p:string)=>path.join(p,'Contents/Resources/app.asar');
 const env=process.env.DAN_LOCAL_UPDATE_PATH;delete process.env.DAN_LOCAL_UPDATE_PATH;
 try {
  for(const p of [installed,candidate]) {await fs.mkdir(path.dirname(asar(p)),{recursive:true});await fs.writeFile(asar(p),'same');}
  await fs.writeFile(marker,JSON.stringify({path:candidate}));
  expect(await findPreparedBuild(marker,installed)).toBeNull();
  await fs.writeFile(asar(candidate),'new build');
  expect(await findPreparedBuild(marker,installed)).toBe(candidate);
  await fs.writeFile(asar(installed),'new build');
  expect(await findPreparedBuild(marker,installed)).toBeNull();
  await fs.rm(candidate,{recursive:true});expect(await findPreparedBuild(marker,installed)).toBeNull();
 } finally {if(env===undefined) delete process.env.DAN_LOCAL_UPDATE_PATH;else process.env.DAN_LOCAL_UPDATE_PATH=env;await fs.rm(root,{recursive:true,force:true});}
});
it('turns provider dumps into bounded user-facing messages',()=>{
 const message=updateErrorMessage(new Error('Unable to find latest version https://api.github.com/repos/a/b/releases/latest HttpError:404 Headers: {secret} at handler (/app:12)'));
 expect(message).toContain('No published release');expect(message).not.toContain('secret');expect(message).not.toContain('Headers');
 expect(updateErrorMessage(new Error('Finish active work'))).toBe('Finish active work');
 expect(updateErrorMessage('HttpError:401 Headers: bearer secret')).toContain('authorize');
});
