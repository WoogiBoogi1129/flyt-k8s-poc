// HAMi session plane. No MongoDB, GPU placement, quota arithmetic or CUDA API code.
use std::{collections::HashSet, fs, net::{Ipv4Addr, Shutdown, TcpListener, TcpStream},
    os::unix::net::{UnixListener, UnixStream}, io::{BufRead, BufReader, Write},
    sync::{Arc, Mutex, atomic::{AtomicUsize, Ordering}}, thread, time::{Duration, Instant}};
use serde_json::{json, Value};
use crate::{model::{Binding, ClientKey, Phase, Session, State, WorkerKey}, worker::Worker,
    wire::{identity, nonce, text, token, Result, Wire, PROTOCOL}};

pub struct Manager { state: Mutex<State>, epoch: String }
impl Manager {
    fn new() -> Self {Self {state:Mutex::new(State::default()),epoch:nonce()}}
    fn envelope(&self, mut v: Value) -> Value {v["epoch"]=json!(self.epoch);v["session_protocol"]=json!(PROTOCOL);v}
    fn invalid(&self, worker: &Arc<Worker>) {
        // Shutdown does not need the Worker I/O mutex; it wakes a blocked command.
        worker.close();
        let mut s=self.state.lock().unwrap();
        if !s.nodes.get(&worker.key).map(|n| Arc::ptr_eq(n,worker)).unwrap_or(false) {return;}
        s.nodes.remove(&worker.key);
        s.bindings.retain(|_,b| b.node()!=worker.key);
        let ids:Vec<_>=s.sessions.values().filter(|v| v.binding.node()==worker.key).map(|v| v.id.clone()).collect();
        for id in ids {Self::forget(&mut s,&id);}
    }
    fn forget(s: &mut State, id: &str) {
        if let Some(v)=s.sessions.remove(id) {
            // An old session never erases the secondary index of its replacement.
            if s.clients.get(&v.key).map(String::as_str)==Some(id) {s.clients.remove(&v.key);}
            let _=v.guest_socket.shutdown(Shutdown::Both);
        }
    }
    fn closing(&self, id: &str) {
        let mut s=self.state.lock().unwrap();
        if let Some(v)=s.sessions.get_mut(id) {
            if v.phase==Phase::Active {
                v.phase=Phase::Closing; v.close_at=Some(Instant::now()+Duration::from_secs(10));
                let key=v.key.clone();
                if s.clients.get(&key).map(String::as_str)==Some(id) {s.clients.remove(&key);}
            }
        }
    }
    fn cleanup(self: &Arc<Self>) {
        let jobs:Vec<_>={
            let mut s=self.state.lock().unwrap();
            let now=Instant::now();
            let due:Vec<_>=s.sessions.values().filter(|v| v.phase==Phase::Closing && v.close_at.map(|t|t<=now).unwrap_or(false))
                .map(|v| (v.id.clone(),v.binding.node(),v.rpc_id)).collect();
            let mut jobs=Vec::new();
            let mut claimed=HashSet::new();
            for (id,key,rpc) in due {
                if let Some(n)=s.nodes.get(&key).cloned() {
                    if !claimed.contains(&key) {
                        if n.cleaning.compare_exchange(false,true,Ordering::SeqCst,Ordering::SeqCst).is_err() {continue;}
                        claimed.insert(key);
                    }
                    if let Some(v)=s.sessions.get_mut(&id) {v.close_at=None;}
                    jobs.push((id,n,rpc));
                } else {Self::forget(&mut s,&id);}
            }
            jobs
        };
        // At most 32 sessions per Worker are admitted locally; group by Worker so
        // a blocked cleanup cannot hold a Manager-wide lock or spawn per-session IO threads.
        let mut groups=std::collections::HashMap::new();
        for (id,n,rpc) in jobs {groups.entry(n.key.clone()).or_insert_with(Vec::new).push((id,n,rpc));}
        for (_,jobs) in groups {
            let manager=Arc::clone(self);
            thread::spawn(move || {
                let worker=Arc::clone(&jobs[0].1);
                for (id,n,rpc) in jobs {
                    if let Some(rpc)=rpc {if n.release(rpc).is_err() {manager.invalid(&n);break;}}
                    Self::forget(&mut manager.state.lock().unwrap(),&id);
                }
                worker.cleaning.store(false,Ordering::SeqCst);
            });
        }
    }
    fn register(self: &Arc<Self>, stream: TcpStream) -> Result<()> {
        let ip=stream.peer_addr().map_err(|e|e.to_string())?.ip().to_string();
        ip.parse::<Ipv4Addr>().map_err(|_| "IPv4 required")?;
        let mut wire=Wire::new(stream)?;let hello=wire.value(5)?;
        if hello.get("protocol").and_then(Value::as_str)!=Some("flyt-worker-v3") {return Err("Worker protocol mismatch".into());}
        let key=WorkerKey {pod_uid:text(&hello,"pod_uid")?.into(),generation:text(&hello,"generation")?.into()};
        let n=Arc::new(Worker::new(key,ip,wire)?);n.probe()?;
        {
            let mut s=self.state.lock().unwrap();
            if s.nodes.len()>=128 || s.nodes.values().any(|old| old.ip==n.ip || old.key.pod_uid==n.key.pod_uid) {
                n.close();return Err("Worker identity already registered or capacity reached".into());
            }
            s.nodes.insert(n.key.clone(),Arc::clone(&n));
        }
        let manager=Arc::clone(self);
        thread::spawn(move || {
            while n.alive.load(Ordering::SeqCst) {
                thread::sleep(Duration::from_secs(15));
                if n.probe().is_err() {manager.invalid(&n);break;}
            }
        });
        Ok(())
    }
    fn control(self: &Arc<Self>, v: &Value) -> Result<Value> {
        match v.get("op").and_then(Value::as_str).unwrap_or("") {
            "list" => Ok(json!({"bindings":self.state.lock().unwrap().bindings.values().cloned().collect::<Vec<_>>()})),
            "observe" => {
                let ip=v.get("pod_ip").and_then(Value::as_str).ok_or("pod_ip required")?;
                let uid=text(v,"pod_uid")?;
                let n=self.state.lock().unwrap().nodes.values().find(|n| n.ip==ip && n.key.pod_uid==uid).cloned().ok_or("Worker not registered")?;
                if n.probe().is_err() {self.invalid(&n);return Err("Worker refresh failed".into());}
                let s=self.state.lock().unwrap();
                if !s.nodes.get(&n.key).map(|x| Arc::ptr_eq(x,&n)).unwrap_or(false) || !n.alive.load(Ordering::SeqCst) {return Err("Worker changed".into());}
                Ok(json!({"generation":n.key.generation}))
            },
            "bind" => {
                let b:Binding=serde_json::from_value(v.get("binding").cloned().ok_or("binding required")?).map_err(|_| "invalid binding")?;
                for f in [&b.worker_uid,&b.vmi_uid,&b.pod_uid,&b.generation] {if !identity(f) {return Err("invalid identity".into());}}
                b.vm_ip.parse::<Ipv4Addr>().map_err(|_| "IPv4 required")?;b.pod_ip.parse::<Ipv4Addr>().map_err(|_| "IPv4 required")?;
                let mut s=self.state.lock().unwrap();
                let n=s.nodes.get(&b.node()).ok_or("Worker generation not registered")?;
                if n.ip!=b.pod_ip || !n.alive.load(Ordering::SeqCst) {return Err("Worker is unavailable".into());}
                if let Some(old)=s.bindings.get(&b.worker_uid) {
                    if old==&b {return Ok(json!({"binding":b}));} return Err("unbind previous generation first".into());
                }
                if s.bindings.values().any(|x|x.vm_ip==b.vm_ip || x.vmi_uid==b.vmi_uid || x.pod_ip==b.pod_ip || x.pod_uid==b.pod_uid) {return Err("binding identity reserved".into());}
                s.bindings.insert(b.worker_uid.clone(),b.clone());Ok(json!({"binding":b}))
            },
            "unbind" => {
                let uid=text(v,"worker_uid")?;
                // Same v3 Controller contract: unbind revokes only this Worker's
                // registration and ends its generation, including RPC children.
                let node={let mut s=self.state.lock().unwrap();s.bindings.remove(uid).and_then(|b|s.nodes.get(&b.node()).cloned())};
                if let Some(n)=node {self.invalid(&n);} Ok(json!({}))
            },
            _ => Err("unsupported binding operation".into()),
        }
    }
    fn binding_connection(self: &Arc<Self>, stream: TcpStream) -> Result<()> {
        let mut w=Wire::new(stream)?;
        let response=(|| {
            let v=w.value(5)?;
            let path=std::env::var("FLYT_BINDING_TOKEN_FILE").map_err(|_|"token path missing")?;
            let secret=fs::read_to_string(path).map_err(|_|"token unavailable")?;let secret=secret.trim();
            let supplied=v.get("token").and_then(Value::as_str).unwrap_or("");
            let equal=secret.len()==supplied.len() && secret.bytes().zip(supplied.bytes()).fold(0u8,|a,(b,c)|a|(b^c))==0;
            if !(32..=256).contains(&secret.len()) || !equal {return Err("unauthorized".into());}
            self.control(&v)
        })();
        let result=match response {Ok(mut r)=>{r["ok"]=json!(true);r},Err(e)=>json!({"ok":false,"error":e})};
        w.json(&self.envelope(result))
    }
    fn open(&self, ip: &str, request: &Value, w: &Wire) -> Result<(String,Arc<Worker>)> {
        if request.get("protocol").and_then(Value::as_str)!=Some(PROTOCOL) || request.get("op").and_then(Value::as_str)!=Some("open") {return Err("stage-7 Guest Client Manager required".into());}
        let vmi=text(request,"vmi_uid")?;let worker=text(request,"worker_uid")?;
        let guest=text(request,"guest_instance")?;if !token(guest) {return Err("invalid guest instance".into());}
        let client=request.get("client_id").and_then(Value::as_i64).filter(|n|*n>0 && *n<=i32::MAX as i64).ok_or("invalid client ID")?;
        let mut s=self.state.lock().unwrap();
        let binding=s.bindings.get(worker).filter(|b|b.vm_ip==ip && b.vmi_uid==vmi).cloned().ok_or("guest identity has no active binding")?;
        let n=s.nodes.get(&binding.node()).cloned().ok_or("Worker unavailable")?;
        if !n.alive.load(Ordering::SeqCst) {return Err("Worker closed".into());}
        let key=ClientKey {vmi_uid:vmi.into(),worker_uid:worker.into(),pod_uid:binding.pod_uid.clone(),generation:binding.generation.clone(),guest_instance:guest.into(),client_id:client};
        if s.clients.contains_key(&key) {return Err("client already has a live session; no implicit reuse".into());}
        // Transport/process admission bound, not a GPU quota or placement decision.
        if s.sessions.len()>=4096 || s.sessions.values().filter(|v|v.binding.node()==n.key).count()>=32 {return Err("session capacity reached".into());}
        let id=nonce();if s.sessions.contains_key(&id) {return Err("session ID collision".into());}
        let socket=w.shutdown_handle()?;
        s.clients.insert(key.clone(),id.clone());
        s.sessions.insert(id.clone(),Session {id:id.clone(),key,binding,phase:Phase::Starting,rpc_id:None,close_at:None,guest_socket:socket});
        Ok((id,n))
    }
    fn guest_connection(self: &Arc<Self>, stream: TcpStream) -> Result<()> {
        let ip=stream.peer_addr().map_err(|e|e.to_string())?.ip().to_string();
        let mut w=Wire::new(stream)?;
        let request=match w.value(10) {Ok(v)=>v,Err(_)=>{w.json(&self.envelope(json!({"ok":false,"error":"stage-7 JSON session protocol required"})))?;return Ok(());}};
        let (id,n)=match self.open(&ip,&request,&w) {Ok(v)=>v,Err(e)=>{w.json(&self.envelope(json!({"ok":false,"error":e})))?;return Ok(());}};
        let rpc=match n.allocate() {
            Ok(Ok(rpc))=>rpc,
            Ok(Err(e))=>{let _=w.json(&self.envelope(json!({"ok":false,"error":e})));Self::forget(&mut self.state.lock().unwrap(),&id);return Ok(());},
            Err(_)=>{self.invalid(&n);return Err("uncertain allocation; Worker generation revoked".into());},
        };
        let active={
            let mut s=self.state.lock().unwrap();
            let valid=s.sessions.get(&id).map(|v|s.bindings.get(&v.binding.worker_uid)==Some(&v.binding)).unwrap_or(false)
                && s.nodes.get(&n.key).map(|v|Arc::ptr_eq(v,&n)).unwrap_or(false) && n.alive.load(Ordering::SeqCst);
            if valid {let v=s.sessions.get_mut(&id).unwrap();v.rpc_id=Some(rpc);v.phase=Phase::Active;}
            valid
        };
        if !active {self.invalid(&n);return Err("binding changed while allocating".into());}
        let outcome=(|| {
            w.json(&self.envelope(json!({"ok":true,"session_id":id,"pod_ip":n.ip,"rpc_id":rpc})))?;
            loop {
                let v=w.value(30)?;
                if v.get("session_id").and_then(Value::as_str)!=Some(id.as_str()) || v.get("epoch").and_then(Value::as_str)!=Some(self.epoch.as_str()) {return Err("stale session message".into());}
                let alive=self.state.lock().unwrap().sessions.get(&id).map(|s|s.phase==Phase::Active).unwrap_or(false);
                if !alive {return Err("session revoked".into());}
                match v.get("op").and_then(Value::as_str) {
                    Some("heartbeat")=>w.json(&self.envelope(json!({"ok":true,"session_id":id})))?,
                    Some("close")=>{w.json(&self.envelope(json!({"ok":true,"session_id":id})))?;break;},
                    _=>return Err("unsupported session operation".into()),
                }
            }
            Ok(())
        })();
        self.closing(&id);w.close();outcome
    }
    fn view(&self, request: &Value) -> Result<Value> {
        let s=self.state.lock().unwrap();
        let result=match request.get("op").and_then(Value::as_str) {
            Some("list-bindings")=>json!({"bindings":s.bindings.values().collect::<Vec<_>>()}),
            Some("list-workers")=>json!({"workers":s.nodes.values().map(|n|json!({"identity":n.key,"pod_ip":n.ip,"connection_id":n.connection_id,"registered":n.alive.load(Ordering::SeqCst)})).collect::<Vec<_>>()}),
            Some("list-sessions")=>{
                let after=request.get("after").and_then(Value::as_str).unwrap_or("");
                if !after.is_empty() && !token(after) {return Err("invalid cursor".into());}
                let mut records:Vec<_>=s.sessions.values().filter(|v|v.id.as_str()>after).collect();records.sort_by(|a,b|a.id.cmp(&b.id));
                let more=records.len()>128;records.truncate(128);
                let next=if more {records.last().map(|v|v.id.clone())} else {None};
                json!({"sessions":records.iter().map(|v|json!({"session_id":v.id,"identity":v.key,"state":v.phase,"pod_ip":v.binding.pod_ip,"rpc_id":v.rpc_id})).collect::<Vec<_>>(),"next":next})
            },
            _=>return Err("use list-bindings, list-workers or list-sessions; legacy quota views are unavailable".into()),
        };
        Ok(self.envelope(json!({"ok":true,"schema":"flyt-session-view-v7","resource_authority":"Kubernetes/HAMi","data":result})))
    }
    fn frontend(&self, stream: UnixStream) -> Result<()> {
        stream.set_read_timeout(Some(Duration::from_secs(5))).map_err(|e|e.to_string())?;
        stream.set_write_timeout(Some(Duration::from_secs(5))).map_err(|e|e.to_string())?;
        let mut input=BufReader::new(stream.try_clone().map_err(|e|e.to_string())?);
        // Read a single bounded request, with a total deadline (not a per-byte lease).
        let deadline=Instant::now()+Duration::from_secs(5);let mut raw=Vec::new();
        loop {
            let remaining=deadline.checked_duration_since(Instant::now()).ok_or("frontend timeout")?;
            input.get_ref().set_read_timeout(Some(remaining)).map_err(|e|e.to_string())?;
            let b=input.fill_buf().map_err(|e|e.to_string())?;if b.is_empty(){return Err("frontend closed".into());}
            let count=b.iter().position(|c|*c==b'\n').map(|i|i+1).unwrap_or(b.len());
            if raw.len()+count>8192 {return Err("frontend frame too large".into());}
            let done=b[count-1]==b'\n';raw.extend_from_slice(&b[..count]);input.consume(count);if done {break;}
        }
        let result=serde_json::from_slice(&raw).map_err(|_|"JSON query required".to_string()).and_then(|v|self.view(&v));
        let v=match result {Ok(v)=>v,Err(e)=>self.envelope(json!({"ok":false,"error":e}))};
        writeln!(&stream,"{}",v).map_err(|e|e.to_string())
    }
}

// Bounded accepted connections per listener, preserving binding capacity even
// when guest heartbeats occupy all guest slots. No credentials are logged.
fn listen(manager: Arc<Manager>, listener: TcpListener, maximum: usize, handler: fn(&Arc<Manager>,TcpStream)->Result<()>) {
    let count=Arc::new(AtomicUsize::new(0));
    for stream in listener.incoming() {
        let Ok(stream)=stream else {continue};
        if count.fetch_add(1,Ordering::SeqCst)>=maximum {count.fetch_sub(1,Ordering::SeqCst);continue;}
        let m=Arc::clone(&manager);let counter=Arc::clone(&count);
        thread::spawn(move || {let _=handler(&m,stream);counter.fetch_sub(1,Ordering::SeqCst);});
    }
}
pub fn run() {
    crate::resource_backend::require_controlled();
    assert!(crate::resource_backend::hami(),"session Manager requires HAMi");
    let manager=Arc::new(Manager::new());
    for (port,maximum,handler) in [(12401,32,Manager::register as fn(&Arc<Manager>,TcpStream)->Result<()>),
        (12402,512,Manager::guest_connection),(12404,32,Manager::binding_connection)] {
        let listener=TcpListener::bind(("0.0.0.0",port)).expect("listener bind failed");
        let m=Arc::clone(&manager);thread::spawn(move ||listen(m,listener,maximum,handler));
    }
    let m=Arc::clone(&manager);thread::spawn(move ||loop {thread::sleep(Duration::from_secs(1));m.cleanup();});
    let path="/run/flyt/flyt-frontend-socket";
    if std::path::Path::new(path).exists() {fs::remove_file(path).expect("stale frontend socket removal failed");}
    let listener=UnixListener::bind(path).expect("frontend bind failed");
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path,fs::Permissions::from_mode(0o600)).expect("socket mode failed");
    for stream in listener.incoming() {if let Ok(stream)=stream {let _=manager.frontend(stream);}}
}
