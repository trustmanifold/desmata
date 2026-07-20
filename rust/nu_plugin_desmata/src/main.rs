//! `nu_plugin_desmata` -- a thin nushell-plugin front-end for `dsm anatomy`.
//!
//! The plugin registers a single command, `dsm anatomy`, whose multi-word name
//! shadows exactly that subcommand of the external `dsm` when the plugin is
//! loaded; every other `dsm ...` invocation still falls through to the Python
//! CLI. `run` execs `dsm anatomy <path> --output json` (a direct OS exec, so
//! there is no recursion back through nushell) and rebuilds its json as a
//! nushell record: `{ cell_dir, nucleus: table, membrane: list, artifact_pins:
//! table }`.

use nu_plugin::{
    serve_plugin, EngineInterface, EvaluatedCall, MsgPackSerializer, Plugin, PluginCommand,
    SimplePluginCommand,
};
use nu_protocol::{record, Category, LabeledError, Signature, Span, SyntaxShape, Type, Value};
use std::process::Command;

struct DesmataPlugin;

impl Plugin for DesmataPlugin {
    fn version(&self) -> String {
        env!("CARGO_PKG_VERSION").into()
    }

    fn commands(&self) -> Vec<Box<dyn PluginCommand<Plugin = Self>>> {
        vec![Box::new(Anatomy)]
    }
}

struct Anatomy;

impl SimplePluginCommand for Anatomy {
    type Plugin = DesmataPlugin;

    fn name(&self) -> &str {
        "dsm anatomy"
    }

    fn description(&self) -> &str {
        "A cell's nucleus/membrane split and artifact pins as structured data \
         (wraps the Python `dsm anatomy --output json`)."
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
        _engine: &EngineInterface,
        call: &EvaluatedCall,
        _input: &Value,
    ) -> Result<Value, LabeledError> {
        let span = call.head;
        let path: String = call.opt(0)?.unwrap_or_else(|| ".".to_string());

        let output = Command::new("dsm")
            .args(["anatomy", &path, "--output", "json"])
            .output()
            .map_err(|e| {
                LabeledError::new(format!("could not run `dsm`: {e}"))
                    .with_label("is desmata on PATH?", span)
            })?;

        let json: serde_json::Value = serde_json::from_slice(&output.stdout).map_err(|e| {
            let stderr = String::from_utf8_lossy(&output.stderr);
            LabeledError::new(format!("`dsm anatomy` did not return json: {e}"))
                .with_label(stderr.trim().to_string(), span)
        })?;

        // The Python CLI reports a non-cell directory as a json error object
        // (and exits non-zero); surface it as a nushell error, not a record.
        if let Some(err) = json.get("error").and_then(|v| v.as_str()) {
            let missing = json
                .get("missing")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|v| v.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .unwrap_or_default();
            return Err(LabeledError::new(format!("dsm anatomy: {err}"))
                .with_label(format!("missing {missing}"), span));
        }

        let nucleus = json_array(&json, "nucleus", span, |item, span| {
            Value::record(
                record! {
                    "name" => json_str(item, "name", span),
                    "present" => Value::bool(
                        item.get("present").and_then(|v| v.as_bool()).unwrap_or(false),
                        span,
                    ),
                },
                span,
            )
        });

        let membrane = json_array(&json, "membrane", span, |item, span| {
            Value::string(item.as_str().unwrap_or_default(), span)
        });

        let artifact_pins = json_array(&json, "artifact_pins", span, |item, span| {
            Value::record(
                record! {
                    "name" => json_str(item, "name", span),
                    "pin" => json_str(item, "pin", span),
                },
                span,
            )
        });

        Ok(Value::record(
            record! {
                "cell_dir" => json_str(&json, "cell_dir", span),
                "nucleus" => nucleus,
                "membrane" => membrane,
                "artifact_pins" => artifact_pins,
            },
            span,
        ))
    }
}

/// A string field of a json object as a nushell string Value (empty if absent).
fn json_str(obj: &serde_json::Value, key: &str, span: Span) -> Value {
    Value::string(obj.get(key).and_then(|v| v.as_str()).unwrap_or_default(), span)
}

/// Map a json array field into a nushell list, one Value per element. A missing
/// or non-array field yields an empty list.
fn json_array(
    obj: &serde_json::Value,
    key: &str,
    span: Span,
    f: impl Fn(&serde_json::Value, Span) -> Value,
) -> Value {
    let items = obj
        .get(key)
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().map(|item| f(item, span)).collect())
        .unwrap_or_default();
    Value::list(items, span)
}

fn main() {
    serve_plugin(&DesmataPlugin, MsgPackSerializer)
}
