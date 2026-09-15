use std::{collections::HashMap, net::{Ipv4Addr, SocketAddr, TcpStream}, sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}}, thread, time::Duration};
use serde_json::{json, Value};
use crate::wire::{nonce, token, Result, Wire, PROTOCOL};

struct Lease { stop: AtomicBool, socket: TcpStream, session_id: String }
pub struct Guest {
    endpoint: SocketAddr, vmi_uid: String, worker_uid: String, instance: String,
    leases: Mutex<HashMap<i32,Arc<Lease>>>,
}
impl Guest {
    pub fn new(endpoint: SocketAddr, vmi_uid: String, worker_uid: String) -> Self {
        Self {endpoint,vmi_uid,worker_uid,instance:nonce(),leases:Mutex::new(HashMap::new())}
    }
    fn close(&self, gid: i32) {
        if let Some(v)=self.leases.lock().unwrap().remove(&gid) {
            v.stop.store(true,Ordering::SeqCst);
            // Closing this connection can only close its server-assigned session.
            let _=v.socket.shutdown(std::net::Shutdown::Both);
        }
    }
    pub fn reap(&self, gid: i32) {self.close(gid);}
    pub fn get<'a>(&'a self, scope: &'a thread::Scope<'a,'_>, gid: i32, connect: bool) -> Option<(String,u64)> {
        if !connect {self.close(gid);return None;}
        if gid<=0 || self.leases.lock().unwrap().contains_key(&gid) {return None;}
        match self.open(scope,gid) {Ok(v)=>Some(v),Err(_)=>{log::warn!("Session allocation failed; no legacy fallback or transparent reconnect");None}}
    }
    fn open<'a>(&'a self, scope: &'a thread::Scope<'a,'_>, gid: i32) -> Result<(String,u64)> {
        let stream=TcpStream::connect_timeout(&self.endpoint,Duration::from_secs(5)).map_err(|e|e.to_string())?;
        let mut w=Wire::new(stream)?;
        w.json(&json!({"protocol":PROTOCOL,"op":"open","vmi_uid":self.vmi_uid,"worker_uid":self.worker_uid,
            "guest_instance":self.instance,"client_id":gid}))?;
        let reply=w.value(80)?;
        if reply.get("ok").and_then(Value::as_bool)!=Some(true) || reply.get("session_protocol").and_then(Value::as_str)!=Some(PROTOCOL) {return Err("Manager protocol/allocation rejected".into());}
        let id=reply.get("session_id").and_then(Value::as_str).filter(|s|token(s)).ok_or("session ID missing")?.to_string();
        let epoch=reply.get("epoch").and_then(Value::as_str).filter(|s|token(s)).ok_or("epoch missing")?.to_string();
        let ip=reply.get("pod_ip").and_then(Value::as_str).ok_or("endpoint missing")?.parse::<Ipv4Addr>().map_err(|_|"invalid endpoint")?.to_string();
        let rpc=reply.get("rpc_id").and_then(Value::as_u64).filter(|n|*n>0).ok_or("RPC ID missing")?;
        let lease=Arc::new(Lease {stop:AtomicBool::new(false),socket:w.shutdown_handle()?,session_id:id});
        {
            let mut leases=self.leases.lock().unwrap();
            if leases.contains_key(&gid) {w.close();return Err("client already connected".into());}
            leases.insert(gid,Arc::clone(&lease));
        }
        scope.spawn(move || {
            while !lease.stop.load(Ordering::SeqCst) {
                thread::sleep(Duration::from_secs(5));
                if lease.stop.load(Ordering::SeqCst) {break;}
                if w.json(&json!({"op":"heartbeat","session_id":lease.session_id,"epoch":epoch})).is_err() {break;}
                match w.value(10) {
                    Ok(v) if v.get("ok").and_then(Value::as_bool)==Some(true) && v.get("session_id").and_then(Value::as_str)==Some(lease.session_id.as_str()) && v.get("epoch").and_then(Value::as_str)==Some(epoch.as_str())=>{},
                    _=>break,
                }
            }
            w.close();lease.stop.store(true,Ordering::SeqCst);
            let mut leases=self.leases.lock().unwrap();
            if leases.get(&gid).map(|v|Arc::ptr_eq(v,&lease)).unwrap_or(false) {leases.remove(&gid);}
            log::info!("Session control connection ended; CUDA session is not restored");
        });
        Ok((ip,rpc))
    }
}
