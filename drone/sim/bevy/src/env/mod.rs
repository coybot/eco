//! Environments. Manhattan is the first; procedural generators implement the
//! same `Environment` trait.

pub mod manhattan;

use crate::scenario::{Environment, Scenario};

/// Build the environment named by the scenario, or `None` if unknown.
///
/// The Godot build silently fell back to the office env for any unrecognised
/// name (`fleet_manager.gd`'s `env_map.get(env_name, office)`). Returning
/// `None` instead is deliberate: this binary only implements Manhattan, and
/// quietly flying a different world than the one asked for is exactly the kind
/// of failure that wastes an evaluation run.
pub fn build(scenario: &Scenario) -> Option<Box<dyn Environment>> {
    match scenario.name.as_str() {
        "manhattan" => Some(Box::new(manhattan::Manhattan::load(scenario))),
        _ => None,
    }
}
