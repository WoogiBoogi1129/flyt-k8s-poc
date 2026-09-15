use std::{io::{BufRead, BufReader, Read, Write}, net::{Shutdown, TcpStream}, time::{Duration, Instant}};
use serde_json::Value;

pub const PROTOCOL: &str = "flyt-session-v7";
pub type Result<T> = std::result::Result<T,String>;

pub fn nonce() -> String {
    let mut bytes = [0u8; 16];
    std::fs::File::open("/dev/urandom").expect("entropy unavailable").read_exact(&mut bytes).expect("entropy read failed");
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}
pub fn identity(s: &str) -> bool { !s.is_empty() && s.len() <= 128 && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') }
pub fn token(s: &str) -> bool { s.len() == 32 && s.bytes().all(|b| b.is_ascii_hexdigit()) }
pub fn text<'a>(v: &'a Value, k: &str) -> Result<&'a str> {
    v.get(k).and_then(Value::as_str).filter(|s| identity(s)).ok_or_else(|| format!("missing/invalid {}", k))
}
pub struct Wire { pub reader: BufReader<TcpStream>, writer: TcpStream }
impl Wire {
    pub fn new(stream: TcpStream) -> Result<Self> {
        stream.set_nodelay(true).map_err(|e| e.to_string())?;
        let copy = stream.try_clone().map_err(|e| e.to_string())?;
        Ok(Self { reader: BufReader::new(copy), writer: stream })
    }
    pub fn shutdown_handle(&self) -> Result<TcpStream> { self.writer.try_clone().map_err(|e| e.to_string()) }
    pub fn close(&self) { let _ = self.writer.shutdown(Shutdown::Both); }
    pub fn send(&mut self, s: &str) -> Result<()> {
        self.writer.set_write_timeout(Some(Duration::from_secs(5))).map_err(|e| e.to_string())?;
        self.writer.write_all(s.as_bytes()).map_err(|e| e.to_string())
    }
    pub fn json(&mut self, v: &Value) -> Result<()> { self.send(&(v.to_string()+"\n")) }
    pub fn line(&mut self, seconds: u64) -> Result<String> {
        let deadline = Instant::now() + Duration::from_secs(seconds);
        let mut out = Vec::new();
        loop {
            let remaining = deadline.checked_duration_since(Instant::now()).ok_or("line deadline expired")?;
            self.reader.get_ref().set_read_timeout(Some(remaining)).map_err(|e| e.to_string())?;
            let buf = self.reader.fill_buf().map_err(|e| e.to_string())?;
            if buf.is_empty() { return Err("peer closed".into()); }
            let n = buf.iter().position(|b| *b == b'\n').map(|i| i+1).unwrap_or(buf.len());
            if out.len()+n > 8192 { return Err("frame exceeds 8192 bytes".into()); }
            let done = buf[n-1] == b'\n';
            out.extend_from_slice(&buf[..n]); self.reader.consume(n);
            if done { return String::from_utf8(out).map(|s| s.trim_end_matches(|c| c=='\r' || c=='\n').to_string()).map_err(|_| "invalid UTF-8".into()); }
        }
    }
    pub fn value(&mut self, seconds: u64) -> Result<Value> { serde_json::from_str(&self.line(seconds)?).map_err(|_| "invalid JSON frame".into()) }
}
