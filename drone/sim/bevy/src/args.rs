//! Command-line parsing that matches Godot's `IpcServer._get_launch_arg`.
//!
//! The Python harnesses launch the engine with the sim's own flags after a bare
//! `--` (see `fw_eval.launch_flightline`: `["--", "--fleet=", "--env=manhattan",
//! "--seed=0", "--ipc-port=9979"]`), and Godot accepts both `--key=value` and
//! `--key value`. Both forms are supported here so the same argv works against
//! either engine. A leading standalone `--` is skipped, so the Godot-style
//! invocation can be passed through verbatim.

/// Fetch a launch argument, or `default` when it is absent.
pub fn launch_arg(key: &str, default: &str) -> String {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let eq = format!("{key}=");
    for (i, a) in args.iter().enumerate() {
        if let Some(rest) = a.strip_prefix(&eq) {
            return rest.to_string();
        }
        if a == key {
            if let Some(next) = args.get(i + 1) {
                return next.clone();
            }
        }
    }
    default.to_string()
}

pub fn launch_arg_f32(key: &str, default: f32) -> f32 {
    launch_arg(key, "").parse().unwrap_or(default)
}

pub fn launch_arg_u16(key: &str, default: u16) -> u16 {
    launch_arg(key, "").parse().unwrap_or(default)
}

pub fn launch_arg_u64(key: &str, default: u64) -> u64 {
    launch_arg(key, "").parse().unwrap_or(default)
}
