//! Resource control policy, shared by Manager, Node Manager and local CLI.
use std::{env, sync::OnceLock};

#[derive(Clone, Copy, PartialEq)]
pub enum Backend { LegacyMps, Hami }
pub const UNSUPPORTED: &str = "HAMi Pod quotas are immutable; update FlytGPURequest and create a new VMI";
pub fn selected() -> Backend {
    static VALUE: OnceLock<Backend> = OnceLock::new();
    *VALUE.get_or_init(|| {
        let result = match env::var("FLYT_RESOURCE_BACKEND") {
            Ok(value) if value == "hami" => Backend::Hami,
            Ok(value) if value == "mps" && option_env!("FLYT_BUILD_BACKEND") != Some("hami") => Backend::LegacyMps,
            Err(env::VarError::NotPresent) if option_env!("FLYT_BUILD_BACKEND") != Some("hami") => Backend::LegacyMps,
            _ => panic!("Invalid FLYT_RESOURCE_BACKEND for this build; no automatic backend fallback"),
        };
        if result == Backend::Hami && env::vars_os().any(|(k,_)| k.to_string_lossy().starts_with("CUDA_MPS_")) {
            panic!("HAMi backend rejects CUDA_MPS_* settings");
        }
        result
    })
}
pub fn hami() -> bool { selected() == Backend::Hami }
pub fn require_legacy() -> Result<(),String> { if hami() {Err(UNSUPPORTED.into())} else {Ok(())} }
pub fn require_controlled() {
    if hami() && env::var("FLYT_BINDING_API").as_deref() != Ok("1") {
        panic!("stage-5 HAMi runtime requires the controlled binding protocol");
    }
}
fn number(name: &str, low: u64, high: u64) -> Result<u64,String> {
    let value = env::var(name).map_err(|_| format!("missing {}",name))?;
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {return Err(format!("invalid {}",name));}
    let n = value.parse::<u64>().map_err(|_| format!("invalid {}",name))?;
    if n < low || n > high {return Err(format!("{} out of range",name));}
    Ok(n)
}
pub struct WorkerSettings { pub capability_sm: u32, pub memory_bytes: u64, pub max_clients: usize }
pub fn worker_settings() -> Result<WorkerSettings,String> {
    if !hami() {return Err("HAMi settings requested for a legacy backend".into());}
    let uuid = env::var("FLYT_GPU_UUID").map_err(|_| "GPU UUID required")?;
    if uuid.len()!=40 || !uuid.starts_with("GPU-") || !uuid[4..].bytes().enumerate().all(|(i,b)| {
        if [8,13,18,23].contains(&i) {b==b'-'} else {b.is_ascii_hexdigit()}
    }) {return Err("invalid GPU UUID".into());}
    if env::var("GPU_CORE_UTILIZATION_POLICY").as_deref()!=Ok("force") {return Err("HAMi force policy required".into());}
    Ok(WorkerSettings {
        capability_sm:number("FLYT_REPORTED_SM",1,u32::MAX as u64)? as u32,
        memory_bytes:number("FLYT_MEMORY_BYTES",256*1024*1024,1048576*1024*1024)?,
        max_clients:number("FLYT_MAX_CLIENTS",1,32)? as usize,
    })
}
