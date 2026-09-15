use std::{collections::HashMap, net::TcpStream, sync::Arc, time::Instant};
use serde::{Deserialize, Serialize};
use crate::worker::Worker;

// Controller v3 binding shape intentionally preserved; no quota copy is accepted.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Binding {
    pub worker_uid: String, pub vmi_uid: String, pub vm_ip: String,
    pub pod_uid: String, pub pod_ip: String, pub generation: String,
}
#[derive(Clone, Debug, Hash, PartialEq, Eq, Serialize)]
pub struct WorkerKey { pub pod_uid: String, pub generation: String }
impl Binding { pub fn node(&self) -> WorkerKey { WorkerKey { pod_uid:self.pod_uid.clone(), generation:self.generation.clone() } } }
#[derive(Clone, Debug, Hash, PartialEq, Eq, Serialize)]
pub struct ClientKey {
    pub vmi_uid: String, pub worker_uid: String, pub pod_uid: String, pub generation: String,
    pub guest_instance: String, pub client_id: i64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum Phase { Starting, Active, Closing }
pub struct Session {
    pub id: String, pub key: ClientKey, pub binding: Binding, pub phase: Phase,
    pub rpc_id: Option<u64>, pub close_at: Option<Instant>, pub guest_socket: TcpStream,
}
#[derive(Default)]
pub struct State {
    pub bindings: HashMap<String, Binding>, // Worker UID is the primary binding key.
    pub nodes: HashMap<WorkerKey, Arc<Worker>>,
    pub sessions: HashMap<String, Session>, // Opaque session ID is the primary session key.
    pub clients: HashMap<ClientKey, String>, // Secondary lookup only; cleanup compares session IDs.
}
