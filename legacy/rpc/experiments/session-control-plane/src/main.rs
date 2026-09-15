#[cfg(feature="legacy-mps")]
compile_error!("build HAMi session binaries with --no-default-features --features hami-control-plane");
mod wire;
mod model;
mod worker;
mod manager;
#[path="../common/resource_backend.rs"] mod resource_backend;
fn main() { env_logger::init(); manager::run(); }
