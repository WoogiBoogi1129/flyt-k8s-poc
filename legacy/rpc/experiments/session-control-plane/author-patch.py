#!/usr/bin/env python3
"""Author the stage-7 diff from source assets; does not apply/build/check patches.

Input is the preserved baseline-patched Flyt authoring source. Cargo.toml and
vcuda_client_handler.rs have no stage-2/3/5 edits; all other additions are new files.
"""
import argparse
import difflib
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
a = p.parse_args()
root = Path(__file__).resolve().parent
changes = {}

def update(path, before, after):
    changes[path] = (before, after)

def replace(text, old, new):
    if old not in text:
        raise ValueError("authoring anchor missing")
    return text.replace(old, new, 1)

path = "control-managers/Cargo.toml"
before = (a.source / path).read_text()
value = replace(before, '[dependencies]', '''[features]
default = ["legacy-mps"]
legacy-mps = ["dep:mongodb"]
hami-control-plane = []

[dependencies]''')
value = replace(value, 'mongodb = { version = "2.8.2", features = ["tokio-sync"] }',
                'mongodb = { version = "2.8.2", features = ["tokio-sync"], optional = true }')
for name in ("flyt-client-manager", "flyt-cluster-manager", "flytctl", "flytctlnet"):
    value = replace(value, 'name = "' + name + '"', 'name = "' + name + '"\nrequired-features = ["legacy-mps"]')
for name, source in (("flyt-session-manager", "main.rs"), ("flyt-session-client-manager", "guest_main.rs"), ("flyt-sessionctl", "cli.rs")):
    value += '\n[[bin]]\nname = "' + name + '"\npath = "src/session-control-plane/' + source + '"\nrequired-features = ["hami-control-plane"]\n'
update(path, before, value)

path = "control-managers/src/client-manager-daemon/vcuda_client_handler.rs"
before = (a.source / path).read_text()
value = replace(before, 'struct ClientMessageTypeId {', '''struct ClientMessageTypeId {
    #[cfg(feature="hami-control-plane")]
    start_ticks: Option<u64>,''')
value = replace(value, '    pub fn get_client_gid(&self, pid: u32) -> i32 {', '''    #[cfg(feature="hami-control-plane")]
    fn process_start_ticks(pid: u32) -> Option<u64> {
        let stat=std::fs::read_to_string(format!("/proc/{}/stat",pid)).ok()?;
        stat.rsplit_once(')')?.1.split_whitespace().nth(19)?.parse().ok()
    }

    #[cfg(feature="hami-control-plane")]
    pub fn reap_session_clients<F>(&self, notify: F) where F: Fn(i32) {
        // Snapshot identities, inspect /proc without a client lock, then remove
        // only the exact old entry. Never notify an IP-wide ZERO_CLIENTS command.
        let snapshot:Vec<_>=self.clients.read().unwrap().iter().map(|c|(c.send_id,c.gid,c.start_ticks)).collect();
        for (pid,gid,start) in snapshot {
            if start.is_some() && Self::process_start_ticks(pid as u32)==start {continue;}
            let removed={
                let mut clients=self.clients.write().unwrap();
                if let Some(index)=clients.iter().position(|c|c.send_id==pid && c.gid==gid && c.start_ticks==start) {clients.remove(index);true} else {false}
            };
            if removed {notify(gid);}
        }
    }

    pub fn get_client_gid(&self, pid: u32) -> i32 {''')
value = replace(value, '            if client.send_id as u32  == pid {', '''            if client.send_id as u32  == pid {
                #[cfg(feature="hami-control-plane")]
                if client.start_ticks.is_none() || client.start_ticks!=Self::process_start_ticks(pid) {continue;}''')
value = replace(value, '                let client_msgid = ClientMessageTypeId {', '''                #[cfg(feature="hami-control-plane")]
                let start_ticks=Self::process_start_ticks(client_pid);
                #[cfg(feature="hami-control-plane")]
                if start_ticks.is_none() || gid<=0 {
                    let bytes=MqueueClientControlCommand::new("500", "").as_bytes();
                    let _=message_queue.send(&bytes,client_pid as i64);
                    continue;
                }
                let client_msgid = ClientMessageTypeId {
                    #[cfg(feature="hami-control-plane")]
                    start_ticks,''')
value = replace(value, '                                log::error!("Error sending virt server details to client: {}", client_pid);', '''                                log::error!("Error sending virt server details to client: {}", client_pid);
                                #[cfg(feature="hami-control-plane")]
                                { let _=virt_server_getter(gid,sm_core,false); }''')
update(path, before, value)
for source in sorted((root / "src").glob("*.rs")):
    update("control-managers/src/session-control-plane/" + source.name, "", source.read_text())
patch = ""
for name, (before, after) in changes.items():
    patch += f"diff --git a/{name} b/{name}\n"
    patch += "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                     fromfile="a/"+name if before else "/dev/null", tofile="b/"+name))
(root / "patches/0001-session-control-plane.patch").write_text(patch)
