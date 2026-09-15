#[cfg(feature="legacy-mps")]
compile_error!("stage-7 Guest requires the isolated hami-control-plane feature");
mod wire;
mod guest;
#[path="../common/mod.rs"] mod common;
#[path="../client-manager-daemon/vcuda_client_handler.rs"] mod vcuda_client_handler;
use std::{net::{IpAddr, SocketAddr}, thread, time::Duration};

fn main() {
    env_logger::init();
    assert!(!std::env::vars_os().any(|(k,_)|k.to_string_lossy().starts_with("CUDA_MPS_")),"stage-7 Guest rejects CUDA_MPS settings");
    let config=common::utils::Utils::load_config_file(common::config::CLMGR_CONFIG_PATH);
    let session=config.get("session-control").expect("stage-7 session-control config required");
    assert_eq!(session.get("protocol").and_then(|v|v.as_str()),Some(wire::PROTOCOL),"Guest control protocol mismatch");
    let vmi=session.get("vmi-uid").and_then(|v|v.as_str()).filter(|v|wire::identity(v)).expect("VMI UID required");
    let worker=session.get("worker-uid").and_then(|v|v.as_str()).filter(|v|wire::identity(v)).expect("Worker UID required");
    let address=config["resource-manager"]["address"].as_str().expect("Manager IP required").parse::<IpAddr>().expect("literal Manager IP required");
    let port=config["resource-manager"]["port"].as_integer().filter(|v|*v>0 && *v<=65535).expect("invalid Manager port") as u16;
    let path=config["ipc"]["mqueue-path"].as_str().expect("IPC path required");
    let clients=vcuda_client_handler::VCudaClientManager::new(path);
    let manager=guest::Guest::new(SocketAddr::new(address,port),vmi.into(),worker.into());
    thread::scope(|scope| {
        scope.spawn(|| clients.listen_to_clients(|gid,_legacy_sm,connect|manager.get(scope,gid,connect)));
        scope.spawn(|| loop {thread::sleep(Duration::from_secs(5));clients.reap_session_clients(|gid|manager.reap(gid));});
    });
}
