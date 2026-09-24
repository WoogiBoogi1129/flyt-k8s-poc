#!/usr/bin/env node
// NODE_PATH must resolve an installed Playwright. Captures a live browser page.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
 const dest=process.argv[2];if(!dest)throw Error('usage: capture-evidence-dashboard.cjs NEW_OUTPUT_DIRECTORY');
 fs.mkdirSync(dest,{recursive:false});
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1800,height:1300},deviceScaleFactor:1});
 await page.goto('http://127.0.0.1:8765');
 const seen=new Set();
 async function capture(label){
  const before=await page.evaluate(()=>window.evidenceSnapshot);
  const png=await page.screenshot({fullPage:true});
  const after=await page.evaluate(()=>window.evidenceSnapshot);
  if(before.sequence!==after.sequence)return false;
  fs.writeFileSync(path.join(dest,label+'.png'),png);
  fs.writeFileSync(path.join(dest,label+'.json'),JSON.stringify({captured_at:new Date().toISOString(),url:page.url(),browser:browser.version(),viewport:{width:1800,height:1300},snapshot:before},null,2)+'\n');
  console.log('Captured '+label+' snapshot '+before.sequence);return true;
 }
 while(!fs.existsSync(path.join(dest,'STOP'))){
  const d=await page.evaluate(()=>window.evidenceSnapshot);
  if(d?.sequence && !d.errors.length){
   const ready=d.routes.filter(r=>r.phase==='Ready');
   const labels=[];
   if(ready.length>=2 && d.gpu.split('\n').filter(line=>line.startsWith('GPU-') && line.includes('flyt-shm')).length>=2)labels.push('two-vm-gpu-sharing');
   for(const r of ready)if(r.vm.includes('-compute-') && d.guest.includes('MEASUREMENT_START'))labels.push(r.vm+'-load');
   // Only the latest stage per run describes the allocation currently held.
   const latest=new Map();for(const s of d.stages)latest.set(s.run,s.stage);
   for(const [run,stage] of latest)if(ready.some(r=>r.vm===run))labels.push(run+'-'+stage);
   if(d.routes.length && d.routes.every(r=>r.phase==='Released'))labels.push('normal-release');
   for(const label of labels)if(!seen.has(label) && await capture(label))seen.add(label);
  }
  await page.waitForTimeout(700);
 }
 await capture('final-state');await browser.close();
})().catch(e=>{console.error(e);process.exitCode=1});
