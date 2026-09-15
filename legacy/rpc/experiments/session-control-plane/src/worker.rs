use std::{net::TcpStream, sync::{Mutex, atomic::{AtomicBool, Ordering}}};
use crate::{model::WorkerKey, wire::{Result, Wire}};

pub struct Worker {
    pub key: WorkerKey, pub ip: String, pub connection_id: String,
    pub alive: AtomicBool, pub cleaning: AtomicBool, io: Mutex<Wire>, shutdown: TcpStream,
}
impl Worker {
    pub fn new(key: WorkerKey, ip: String, io: Wire) -> Result<Self> {
        Ok(Self { key, ip, connection_id:crate::wire::nonce(), shutdown:io.shutdown_handle()?, io:Mutex::new(io), alive:AtomicBool::new(true), cleaning:AtomicBool::new(false) })
    }
    pub fn close(&self) {
        self.alive.store(false,Ordering::SeqCst);
        let _ = self.shutdown.shutdown(std::net::Shutdown::Both);
    }
    // No global Manager state lock is held during these per-Worker exchanges.
    fn exchange<T>(&self, op: impl FnOnce(&mut Wire)->Result<T>) -> Result<T> {
        let mut wire = self.io.lock().map_err(|_| "Worker channel poisoned")?;
        if !self.alive.load(Ordering::SeqCst) {return Err("Worker is closed".into());}
        match op(&mut wire) {Ok(v)=>Ok(v),Err(e)=>{self.close();Err(e)}}
    }
    pub fn probe(&self) -> Result<()> {
        self.exchange(|w| {
            w.send("RMGR_SNODE_SEND_GPU_INFO\n")?;
            if w.line(5)? != "200" || w.line(5)? != "1" {return Err("Worker must expose one device".into());}
            let line = w.line(5)?; let v:Vec<_> = line.split(',').collect();
            if v.len()!=6 || v[0]!="0" {return Err("invalid Worker inventory".into());}
            // Validate framing, then discard hardware inventory. It is not a quota ledger.
            for i in [2,3,4,5] { v[i].parse::<u64>().map_err(|_| "invalid inventory number")?; }
            Ok(())
        })
    }
    pub fn allocate(&self) -> Result<std::result::Result<u64,String>> {
        self.exchange(|w| {
            // Stage-5 Node Manager derives quota from the immutable Pod environment.
            w.send("RMGR_SNODE_ALLOC_VIRT_SERVER\n0,0,0\n")?;
            let status=w.line(70)?; let payload=w.line(5)?;
            if status=="200" {
                let id=payload.parse::<u64>().map_err(|_| "invalid RPC ID")?;
                if id==0 {return Err("zero RPC ID".into());} Ok(Ok(id))
            } else if status=="400" || status=="500" { Ok(Err("Node Manager rejected session creation".into())) }
            else {Err("invalid allocation response".into())}
        })
    }
    pub fn release(&self, rpc: u64) -> Result<()> {
        self.exchange(|w| {
            w.send(&format!("RMGR_SNODE_DEALLOC_VIRT_SERVER\n{}\n",rpc))?;
            let status=w.line(15)?; let _payload=w.line(5)?;
            if status!="200" {return Err("Node Manager cleanup failed".into());} Ok(())
        })
    }
}
