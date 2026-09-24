#!/usr/bin/env node
// Record the real dashboard while a selected pair runs; no synthetic frames.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
 const [dest,prefix='evidence-show-capture-']=process.argv.slice(2);
 if(!dest)throw Error('usage: record-evidence-dashboard.cjs NEW_DIRECTORY [VM_PREFIX]');
 fs.mkdirSync(dest,{recursive:false});
 let initial;
 const deadline=Date.now()+60*60*1000;
 while(Date.now()<deadline){
  const d=await (await fetch('http://127.0.0.1:8765/snapshot')).json();
  if(d.routes?.filter(r=>r.vm.startsWith(prefix)&&r.phase==='Ready').length===2){initial=d;break;}
  await new Promise(r=>setTimeout(r,1000));
 }
 if(!initial)throw Error('pair did not become Ready');
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1800,height:1500},recordVideo:{dir:dest,size:{width:1800,height:1500}}});
 const page=await context.newPage();
 const started=new Date().toISOString();await page.goto('http://127.0.0.1:8765');
 const end=Date.now()+120000;let final;
 while(Date.now()<end){
  await page.waitForTimeout(1000);final=await page.evaluate(()=>window.evidenceSnapshot);
  const pair=final?.routes?.filter(r=>r.vm.startsWith(prefix))||[];
  if(pair.length===2&&pair.every(r=>r.phase==='Released')){await page.waitForTimeout(3000);break;}
 }
 const video=page.video();await context.close();
 const original=await video.path();const target=path.join(dest,'two-vm-live.webm');fs.renameSync(original,target);
 fs.writeFileSync(path.join(dest,'recording.json'),JSON.stringify({started_at:started,ended_at:new Date().toISOString(),browser:browser.version(),prefix,url:'http://127.0.0.1:8765',initial_snapshot:initial,final_snapshot:final},null,2)+'\n');
 await browser.close();console.log(target);
})().catch(e=>{console.error(e);process.exitCode=1});
