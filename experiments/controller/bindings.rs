// Stage-3 control protocol; CUDA ONC RPC messages are unchanged.
use std::{collections::HashMap, fs, io::{Read, Write}, net::{Shutdown, TcpListener, TcpStream},
          sync::{Mutex, OnceLock, RwLock}, time::Duration};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use crate::{bookkeeping::VMResources, client_handler::FlytClientManager, servernode_handler::ServerNodesManager};

pub fn enabled() -> bool { std::env::var("FLYT_BINDING_API").as_deref() == Ok("1") }
pub fn gate() -> &'static RwLock<()> { static G: OnceLock<RwLock<()>> = OnceLock::new(); G.get_or_init(|| RwLock::new(())) }

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Binding {
    pub worker_uid: String, pub vmi_uid: String, pub vm_ip: String,
    pub pod_uid: String, pub pod_ip: String, pub generation: String,
}
struct Registration { pod_uid: String, generation: String, socket: TcpStream }
#[derive(Default)]
struct State { bindings: HashMap<String, Binding>, nodes: HashMap<String, Registration> }
fn state() -> &'static Mutex<State> { static S: OnceLock<Mutex<State>> = OnceLock::new(); S.get_or_init(|| Mutex::new(State::default())) }
fn epoch() -> &'static str {
    static E: OnceLock<String> = OnceLock::new();
    E.get_or_init(|| { let mut b = [0u8; 16]; fs::File::open("/dev/urandom").unwrap().read_exact(&mut b).unwrap();
        b.iter().map(|v| format!("{:02x}", v)).collect::<String>() })
}
fn text<'a>(v: &'a Value, key: &str) -> Result<&'a str, String> {
    v.get(key).and_then(Value::as_str).filter(|s| !s.is_empty() && s.len() <= 128)
        .ok_or_else(|| format!("invalid {}", key))
}
fn line(stream: &mut TcpStream) -> Result<Value, String> {
    let mut bytes = Vec::new(); let mut byte = [0u8; 1];
    loop {
        if bytes.len() >= 8192 { return Err("request too large".into()); }
        stream.read_exact(&mut byte).map_err(|e| e.to_string())?;
        if byte[0] == b'\n' { break; }
        bytes.push(byte[0]);
    }
    serde_json::from_slice(&bytes).map_err(|_| "invalid JSON request".into())
}
fn secret_matches(input: &str, expected: &str) -> bool {
    if input.len() != expected.len() { return false; }
    input.bytes().zip(expected.bytes()).fold(0u8, |a, (b,c)| a | (b ^ c)) == 0
}
// Caller holds gate write lock. Shutting down the socket wakes Node Manager and
// its supervisor ends that generation's RPC children. No other Worker is closed.
fn drop_node(ip: &str, nodes: &ServerNodesManager, clients: &FlytClientManager) {
    let mut s = state().lock().unwrap();
    let affected: Vec<String> = s.bindings.values().filter(|b| b.pod_ip == ip).map(|b| b.vm_ip.clone()).collect();
    s.bindings.retain(|_, b| b.pod_ip != ip);
    if let Some(n) = s.nodes.remove(ip) { let _ = n.socket.shutdown(Shutdown::Both); }
    drop(s);
    for vm in affected { clients.revoke_controlled_vm(&vm); }
    nodes.remove_server_node(ip);
}
pub fn lookup(ip: &str) -> Option<VMResources> {
    let s = state().lock().unwrap();
    let b = s.bindings.values().find(|b| b.vm_ip == ip)?;
    let n = s.nodes.get(&b.pod_ip)?;
    if n.pod_uid != b.pod_uid || n.generation != b.generation { return None; }
    Some(VMResources { vm_ip: ip.into(), host_ip: b.pod_ip.clone(), compute_units: 0, memory: 0 })
}
// Capture under the control read gate. Delayed cleanup must not operate on a
// replacement Worker that happens to receive the same Pod IP or RPC program ID.
pub fn identity(ip: &str) -> Option<Binding> {
    state().lock().unwrap().bindings.values().find(|b| b.vm_ip == ip).cloned()
}

pub fn nodes(port: u16, nodes: &ServerNodesManager, clients: &FlytClientManager) {
    let listener = TcpListener::bind(("0.0.0.0", port)).expect("node listener bind failed");
    for stream in listener.incoming() {
        let Ok(mut stream) = stream else { continue };
        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
        let Ok(hello) = line(&mut stream) else { continue };
        if hello.get("protocol").and_then(Value::as_str) != Some("flyt-worker-v3") { continue; }
        let (Ok(uid), Ok(generation)) = (text(&hello,"pod_uid"), text(&hello,"generation")) else { continue };
        let Ok(peer) = stream.peer_addr() else { continue };
        if !peer.is_ipv4() { continue; }
        let ip = peer.ip().to_string();
        let Ok(socket) = stream.try_clone() else { continue };
        let _guard = gate().write().unwrap();
        drop_node(&ip, nodes, clients);
        let _ = stream.set_read_timeout(Some(Duration::from_secs(60)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(10)));
        nodes.handle_servernode(stream);
        let ready = nodes.get_server_node(&ip).map(|n| n.gpus.len() == 1 && n.gpus[0].read().unwrap().gpu_id == 0).unwrap_or(false);
        if ready {
            state().lock().unwrap().nodes.insert(ip, Registration {pod_uid:uid.into(), generation:generation.into(), socket});
        } else { let _ = socket.shutdown(Shutdown::Both); nodes.remove_server_node(&ip); }
    }
}

fn request(v: &Value, nodes: &ServerNodesManager, clients: &FlytClientManager) -> Result<Value,String> {
    match text(v, "op")? {
        "ping" => Ok(json!({})),
        "observe" => {
            let ip = text(v,"pod_ip")?; let uid = text(v,"pod_uid")?;
            let generation = {
                let s = state().lock().unwrap();
                let n = s.nodes.get(ip).ok_or("Worker not registered")?;
                if n.pod_uid != uid { return Err("Pod UID differs".into()); }
                n.generation.clone()
            };
            // A real command/response refreshes registration; a local marker alone
            // cannot certify a live generation. No CUDA workload is launched.
            if let Err(e) = nodes.update_server_node_gpus(&ip.to_string()) {
                drop_node(ip, nodes, clients); return Err(format!("registration refresh failed: {}",e));
            }
            Ok(json!({"generation":generation}))
        },
        "bind" => {
            let b: Binding = serde_json::from_value(v.get("binding").cloned().ok_or("missing binding")?).map_err(|e| e.to_string())?;
            for field in [&b.worker_uid,&b.vmi_uid,&b.pod_uid,&b.generation] {
                if field.is_empty() || field.len()>128 { return Err("invalid identity".into()); }
            }
            if b.vm_ip.parse::<std::net::Ipv4Addr>().is_err() || b.pod_ip.parse::<std::net::Ipv4Addr>().is_err() { return Err("IPv4 required".into()); }
            let mut s = state().lock().unwrap();
            let n = s.nodes.get(&b.pod_ip).ok_or("Worker not registered")?;
            if n.pod_uid != b.pod_uid || n.generation != b.generation { return Err("stale Worker generation".into()); }
            if let Some(old) = s.bindings.get(&b.worker_uid) {
                if old == &b { return Ok(json!({"binding":b})); }
                return Err("unbind previous generation before rebinding".into());
            }
            if s.bindings.values().any(|old| old.vm_ip == b.vm_ip || old.vmi_uid == b.vmi_uid || old.pod_ip == b.pod_ip || old.pod_uid == b.pod_uid) {
                return Err("binding identity already reserved".into());
            }
            clients.revoke_controlled_vm(&b.vm_ip);
            s.bindings.insert(b.worker_uid.clone(), b.clone());
            Ok(json!({"binding":b}))
        },
        "unbind" => {
            let uid = text(v,"worker_uid")?;
            let binding = state().lock().unwrap().bindings.get(uid).cloned();
            if let Some(b) = binding { drop_node(&b.pod_ip, nodes, clients); }
            Ok(json!({}))
        },
        "list" => {
            let items: Vec<_> = state().lock().unwrap().bindings.values().cloned().collect();
            Ok(json!({"bindings":items}))
        },
        _ => Err("unknown operation".into()),
    }
}
pub fn serve(nodes: &ServerNodesManager, clients: &FlytClientManager) {
    let _ = epoch();
    let path = std::env::var("FLYT_BINDING_TOKEN_FILE").expect("binding token file required");
    let listener = TcpListener::bind("0.0.0.0:12404").expect("binding listener bind failed");
    for stream in listener.incoming() {
        let Ok(mut stream) = stream else { continue };
        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
        let response = match line(&mut stream) {
            Ok(v) => {
                let token = fs::read_to_string(&path).unwrap_or_default();
                let supplied = v.get("token").and_then(Value::as_str).unwrap_or("");
                if token.trim().len() < 32 || !secret_matches(supplied, token.trim()) {
                    json!({"ok":false,"error":"unauthorized"})
                } else {
                    let _guard = gate().write().unwrap();
                    match request(&v,nodes,clients) {
                        Ok(mut body) => { body["ok"]=json!(true); body["epoch"]=json!(epoch()); body },
                        Err(e) => json!({"ok":false,"error":e,"epoch":epoch()}),
                    }
                }
            },
            Err(_) => json!({"ok":false,"error":"invalid request"}),
        };
        let _ = writeln!(stream,"{}",response);
    }
}
