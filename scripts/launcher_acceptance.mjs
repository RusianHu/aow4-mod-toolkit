// Only reads the copied official renderer and skips its disposable onboarding.
import fs from 'node:fs/promises';
const [port, output] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const snapshots = [], exceptions = [], failures = [];
let ws;
let error;
try {
  let target;
  for (let i = 0; i < 80; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = list.find(t => t.type === 'page' && t.url.includes('/dist/renderer/primary.html'));
      if (target) break;
    } catch {}
    await sleep(100);
  }
  if (!target) throw new Error('No official primary renderer target');
  const url = new URL(target.webSocketDebuggerUrl);
  if (url.hostname !== '127.0.0.1' || url.port !== port) throw new Error('Unexpected debugger endpoint');
  ws = new WebSocket(url);
  await new Promise((resolve, reject) => {ws.onopen=resolve; ws.onerror=reject;});
  let seq=0;
  const pending=new Map();
  ws.onmessage=event => {
    const message=JSON.parse(event.data);
    if (message.id) { const item=pending.get(message.id); pending.delete(message.id); item?.(message); }
    else if (message.method === 'Runtime.exceptionThrown' && exceptions.length < 50) exceptions.push(message.params);
    else if (message.method === 'Network.loadingFailed' && failures.length < 50) failures.push(message.params);
  };
  const call=(method,params={}) => new Promise((resolve,reject) => {
    const id=++seq;
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`Timed out: ${method}`));},5000);
    pending.set(id,m=>{clearTimeout(timer);m.error?reject(new Error(m.error.message)):resolve(m.result);});
    ws.send(JSON.stringify({id,method,params}));
  });
  await call('Runtime.enable'); await call('Network.enable');
  const evaluate=async expression => {
    const r=await call('Runtime.evaluate',{expression,returnByValue:true});
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  };
  for (let i=0;i<3;i++) {
    await sleep(i?5000:1500);
    snapshots.push(JSON.parse(await evaluate(`JSON.stringify({url:location.href,ready:document.readyState,text:document.body?.innerText?.slice(0,10000),buttons:[...document.querySelectorAll('button')].slice(0,80).map(x=>({text:x.innerText,testid:x.getAttribute('data-testid'),disabled:x.disabled})),links:[...document.querySelectorAll('a')].slice(0,40).map(x=>({text:x.innerText,href:x.getAttribute('href')}))})`)));
    if (i===0) await evaluate(`document.querySelector('button[data-testid="stepTracker-skip"]')?.click()`);
    if (i===1) await evaluate(`document.querySelector('a[href="#/mods"]')?.click()`);
  }
} catch(e) {error=String(e);process.exitCode=1;}
finally {
  ws?.close();
  const home_ready=snapshots.some(s=>s.ready==='complete' && s.text?.includes('Age of Wonders 4') && s.buttons?.some(b=>b.testid==='gameButton-play'));
  await fs.writeFile(output,JSON.stringify({error,home_ready,last_text:snapshots.at(-1)?.text||'',snapshots,exceptions,failures},null,2));
}
