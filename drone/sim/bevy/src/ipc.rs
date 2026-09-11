//! TCP IPC server: newline-delimited JSON, one response line per request line.
//!
//! Behavioural contract copied from `ipc_server.gd`, because the Python clients
//! (`engine_client.py`, `rover/sim/depot_client.py`, `fw_manhattan_figure8.py`)
//! are not being changed:
//!
//!   * many concurrent clients, each with its own buffer;
//!   * requests split on `\n`, blank lines ignored;
//!   * exactly one response line per request, IN REQUEST ORDER;
//!   * an unparseable line answers `{"ok": false, "error": "json parse error"}`;
//!   * an unknown op answers `{"ok": false, "error": "unknown op <op>"}`.
//!
//! Ordering is preserved by construction: each client gets a reader thread that
//! blocks on its own reply channel before reading the next line. Godot got the
//! same property for free by dispatching synchronously inside `_process`; here
//! it matters because some ops (frame grabs) need a rendered frame and cannot
//! answer within the dispatch call itself.

use crossbeam_channel::{Receiver, Sender};
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};

/// One request, plus the channel its single response must go back on.
pub struct Request {
    pub body: serde_json::Value,
    pub reply: Sender<String>,
}

/// Main-world end of the IPC server.
pub struct IpcChannel {
    pub rx: Receiver<Request>,
}

/// Bind the port and start accepting. Prints the readiness line the Python
/// harnesses wait for (`fw_eval.py` scans stdout for the substring "IPC ready")
/// only after the listener is actually bound, so a client that connects on
/// seeing it cannot be refused.
pub fn start(port: u16) -> std::io::Result<IpcChannel> {
    let listener = TcpListener::bind(("127.0.0.1", port))?;
    let (tx, rx) = crossbeam_channel::unbounded::<Request>();

    std::thread::Builder::new()
        .name("ipc-accept".into())
        .spawn(move || {
            for stream in listener.incoming() {
                match stream {
                    Ok(stream) => {
                        let tx = tx.clone();
                        std::thread::Builder::new()
                            .name("ipc-client".into())
                            .spawn(move || serve_client(stream, tx))
                            .ok();
                    }
                    Err(e) => eprintln!("[IpcServer] accept failed: {e}"),
                }
            }
        })?;

    // Matches Godot's line exactly.
    println!("[IpcServer] IPC ready on port {port}");
    Ok(IpcChannel { rx })
}

fn serve_client(stream: TcpStream, tx: Sender<Request>) {
    // Nagle off: these are tiny request/response round trips and the Python
    // clients block on each one, so batching adds latency for no gain.
    let _ = stream.set_nodelay(true);
    let mut out = match stream.try_clone() {
        Ok(s) => s,
        Err(_) => return,
    };
    let reader = BufReader::new(stream);
    let (reply_tx, reply_rx) = crossbeam_channel::bounded::<String>(1);

    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break, // client vanished mid-line
        };
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let body: serde_json::Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => {
                if out
                    .write_all(b"{\"ok\":false,\"error\":\"json parse error\"}\n")
                    .is_err()
                {
                    break;
                }
                continue;
            }
        };
        if tx
            .send(Request {
                body,
                reply: reply_tx.clone(),
            })
            .is_err()
        {
            break; // engine is shutting down
        }
        // Block until this request's own reply arrives, which is what keeps
        // responses in request order even when a frame grab has to wait for the
        // renderer.
        match reply_rx.recv() {
            Ok(resp) => {
                if out.write_all(resp.as_bytes()).is_err() || out.write_all(b"\n").is_err() {
                    break;
                }
            }
            Err(_) => break,
        }
    }
}
