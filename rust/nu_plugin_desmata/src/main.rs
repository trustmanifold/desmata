//! `nu_plugin_desmata` -- a generic nushell-plugin bridge over the Python `dsm`.
//!
//! Each command's multi-word name (`dsm anatomy`, `dsm cells`, ...) shadows
//! exactly that subcommand of the external `dsm` when the plugin is loaded;
//! every other `dsm ...` invocation still falls through to the Python CLI.
//! `run` execs `dsm <subcommand> <args...> --output json` (a direct OS exec, so
//! there is no recursion back through nushell) and converts the json into
//! nushell values with one generic converter -- so the Python CLI stays the
//! single source of truth and adding a command is a thin declaration, not a
//! reimplementation of its output.

use nu_plugin::{
    serve_plugin, EngineInterface, EvaluatedCall, MsgPackSerializer, Plugin, PluginCommand,
    SimplePluginCommand,
};
use nu_protocol::{
    Category, LabeledError, Record, Signature, Span, SyntaxShape, Type, Value,
};
use std::process::Command;

struct DesmataPlugin;

impl Plugin for DesmataPlugin {
    fn version(&self) -> String {
        env!("CARGO_PKG_VERSION").into()
    }

    fn commands(&self) -> Vec<Box<dyn PluginCommand<Plugin = Self>>> {
        vec![
            Box::new(Anatomy),
            Box::new(Cells),
            Box::new(Check),
            Box::new(Inspect),
        ]
    }
}

// --- the generic half: run dsm and convert its json -------------------------

/// Recursively convert a `serde_json::Value` into a nushell `Value`. Objects
/// become records, arrays become lists (a list of records renders as a table),
/// and scalars map to the obvious nushell scalar. This is the whole reason a
/// new `dsm` command needs no bespoke Rust: its json shape converts for free.
fn json_to_value(json: &serde_json::Value, span: Span) -> Value {
    match json {
        serde_json::Value::Null => Value::nothing(span),
        serde_json::Value::Bool(b) => Value::bool(*b, span),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Value::int(i, span)
            } else if let Some(u) = n.as_u64() {
                Value::int(u as i64, span)
            } else {
                Value::float(n.as_f64().unwrap_or(0.0), span)
            }
        }
        serde_json::Value::String(s) => Value::string(s, span),
        serde_json::Value::Array(items) => {
            Value::list(items.iter().map(|v| json_to_value(v, span)).collect(), span)
        }
        serde_json::Value::Object(map) => {
            let mut record = Record::with_capacity(map.len());
            for (key, value) in map {
                record.insert(key, json_to_value(value, span));
            }
            Value::record(record, span)
        }
    }
}

/// Exec `dsm <args...> --output json` in `cwd` and convert its stdout. A spawn
/// failure or a non-zero exit (a not-a-cell dir, an unknown cell, ...) becomes
/// a nushell error carrying dsm's own message.
///
/// `cwd` is nushell's current directory (`EngineInterface::get_current_dir`),
/// not the plugin process's: a plugin is a long-lived process whose own cwd
/// does not follow the caller's `cd`, so a relative path (or the `.` default)
/// must be resolved against the caller's directory explicitly.
fn run_dsm_json(args: &[String], cwd: &str, span: Span) -> Result<Value, LabeledError> {
    let output = Command::new("dsm")
        .current_dir(cwd)
        .args(args)
        .args(["--output", "json"])
        .output()
        .map_err(|e| {
            LabeledError::new(format!("could not run `dsm`: {e}"))
                .with_label("is desmata on PATH?", span)
        })?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        let detail = if stderr.trim().is_empty() {
            stdout.trim().to_string()
        } else {
            stderr.trim().to_string()
        };
        return Err(LabeledError::new(format!("dsm {} failed", args.join(" ")))
            .with_label(detail, span));
    }

    let json: serde_json::Value = serde_json::from_slice(&output.stdout).map_err(|e| {
        let stderr = String::from_utf8_lossy(&output.stderr);
        LabeledError::new(format!("`dsm` did not return json: {e}"))
            .with_label(stderr.trim().to_string(), span)
    })?;
    Ok(json_to_value(&json, span))
}

// --- the thin half: one declaration per dsm command -------------------------

struct Anatomy;

impl SimplePluginCommand for Anatomy {
    type Plugin = DesmataPlugin;

    fn name(&self) -> &str {
        "dsm anatomy"
    }

    fn description(&self) -> &str {
        "A cell's nucleus/membrane split and artifact pins as structured data."
    }

    fn signature(&self) -> Signature {
        Signature::build("dsm anatomy")
            .optional(
                "path",
                SyntaxShape::String,
                "the cell directory (default: the current directory)",
            )
            .input_output_type(Type::Nothing, Type::Any)
            .category(Category::Custom("desmata".to_string()))
    }

    fn run(
        &self,
        _plugin: &DesmataPlugin,
        engine: &EngineInterface,
        call: &EvaluatedCall,
        _input: &Value,
    ) -> Result<Value, LabeledError> {
        let path: String = call.opt(0)?.unwrap_or_else(|| ".".to_string());
        run_dsm_json(&["anatomy".to_string(), path], &engine.get_current_dir()?, call.head)
    }
}

struct Cells;

impl SimplePluginCommand for Cells {
    type Plugin = DesmataPlugin;

    fn name(&self) -> &str {
        "dsm cells"
    }

    fn description(&self) -> &str {
        "The cells desmata has local state for, as a structured table."
    }

    fn signature(&self) -> Signature {
        Signature::build("dsm cells")
            .named(
                "home",
                SyntaxShape::String,
                "inspect a sandboxed state dir instead of the XDG dirs",
                None,
            )
            .input_output_type(Type::Nothing, Type::Any)
            .category(Category::Custom("desmata".to_string()))
    }

    fn run(
        &self,
        _plugin: &DesmataPlugin,
        engine: &EngineInterface,
        call: &EvaluatedCall,
        _input: &Value,
    ) -> Result<Value, LabeledError> {
        let mut args = vec!["cells".to_string()];
        if let Some(home) = call.get_flag::<String>("home")? {
            args.push("--home".to_string());
            args.push(home);
        }
        run_dsm_json(&args, &engine.get_current_dir()?, call.head)
    }
}

struct Check;

impl SimplePluginCommand for Check {
    type Plugin = DesmataPlugin;

    fn name(&self) -> &str {
        "dsm check"
    }

    fn description(&self) -> &str {
        "The trusted tools desmata depends on, as a structured table."
    }

    fn signature(&self) -> Signature {
        Signature::build("dsm check")
            .input_output_type(Type::Nothing, Type::Any)
            .category(Category::Custom("desmata".to_string()))
    }

    fn run(
        &self,
        _plugin: &DesmataPlugin,
        engine: &EngineInterface,
        call: &EvaluatedCall,
        _input: &Value,
    ) -> Result<Value, LabeledError> {
        run_dsm_json(&["check".to_string()], &engine.get_current_dir()?, call.head)
    }
}

struct Inspect;

impl SimplePluginCommand for Inspect {
    type Plugin = DesmataPlugin;

    fn name(&self) -> &str {
        "dsm inspect"
    }

    fn description(&self) -> &str {
        "The structure of one tool inside a cell (nix|ipfs|provenance|drv view), \
         as structured data."
    }

    fn signature(&self) -> Signature {
        Signature::build("dsm inspect")
            .required("cell", SyntaxShape::String, "cell name, e.g. 'builtins'")
            .required("tool", SyntaxShape::String, "a managed tool in the cell")
            .required(
                "view",
                SyntaxShape::String,
                "nix | ipfs | provenance | drv",
            )
            .named(
                "depth",
                SyntaxShape::Int,
                "ipfs view: directory levels to expand per store path",
                None,
            )
            .input_output_type(Type::Nothing, Type::Any)
            .category(Category::Custom("desmata".to_string()))
    }

    fn run(
        &self,
        _plugin: &DesmataPlugin,
        engine: &EngineInterface,
        call: &EvaluatedCall,
        _input: &Value,
    ) -> Result<Value, LabeledError> {
        let mut args = vec![
            "inspect".to_string(),
            call.req::<String>(0)?,
            call.req::<String>(1)?,
            call.req::<String>(2)?,
        ];
        if let Some(depth) = call.get_flag::<i64>("depth")? {
            args.push("--depth".to_string());
            args.push(depth.to_string());
        }
        run_dsm_json(&args, &engine.get_current_dir()?, call.head)
    }
}

fn main() {
    serve_plugin(&DesmataPlugin, MsgPackSerializer)
}
