#[cfg(feature="legacy-mps")]
compile_error!("stage-7 CLI requires the isolated hami-control-plane feature");
use std::{io::{BufRead, BufReader, Read, Write}, os::unix::net::UnixStream, time::Duration};
use serde_json::{json, Value};
fn main() {
    let args:Vec<_>=std::env::args().skip(1).collect();
    if args.is_empty() || !["list-bindings","list-workers","list-sessions"].contains(&args[0].as_str()) ||
       !(args.len()==1 || (args.len()==3 && args[0]=="list-sessions" && args[1]=="--after" && args[2].len()==32 && args[2].bytes().all(|b|b.is_ascii_hexdigit()))) {
        eprintln!("usage: flytctl list-bindings | list-workers | list-sessions [--after SESSION_ID]\nStage-7 emits versioned JSON; legacy resource commands/views are unavailable.");std::process::exit(2);
    }
    let result=(|| -> Result<Value,String> {
        let mut stream=UnixStream::connect("/run/flyt/flyt-frontend-socket").map_err(|e|e.to_string())?;
        stream.set_read_timeout(Some(Duration::from_secs(10))).map_err(|e|e.to_string())?;
        stream.set_write_timeout(Some(Duration::from_secs(5))).map_err(|e|e.to_string())?;
        writeln!(stream,"{}",json!({"op":args[0],"after":args.get(2)})).map_err(|e|e.to_string())?;
        let mut raw=Vec::new();BufReader::new(stream).take(1<<20).read_until(b'\n',&mut raw).map_err(|e|e.to_string())?;
        if raw.last()!=Some(&b'\n') {return Err("incomplete/oversized response".into());}
        serde_json::from_slice(&raw).map_err(|_|"invalid response JSON".into())
    })();
    match result {Ok(v)=>{println!("{}",serde_json::to_string_pretty(&v).unwrap());if v.get("ok").and_then(Value::as_bool)!=Some(true){std::process::exit(1);}},Err(e)=>{eprintln!("{}",e);std::process::exit(1);}}
}
