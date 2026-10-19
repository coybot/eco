"""Every DynamoDB call a Lambda's handler can reach is allowed by its IAM policy.

Unit tests run against stand-ins that never check permissions, so a missing
action only shows up live. It did: the operator's OK on a held flight deleted the
held record with DeleteItem, which neither ChatMessageFunction nor
GroupMessageFunction was allowed, and every OK came back as an HTTP 502.

This walks each handler's call graph in the source (same module, plus calls into
another src module through an import alias such as `import conversations as C`)
and collects the DynamoDB operations it can reach. It checks actions, not which
table each one is allowed on.
"""
import ast
import os
import re

import yaml

SRC = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(SRC, "..", "template.yaml")

# boto3 resource/client method -> IAM action.
DDB_ACTIONS = {
    "put_item": "dynamodb:PutItem",
    "get_item": "dynamodb:GetItem",
    "update_item": "dynamodb:UpdateItem",
    "delete_item": "dynamodb:DeleteItem",
    "query": "dynamodb:Query",
    "scan": "dynamodb:Scan",
    "batch_get_item": "dynamodb:BatchGetItem",
    "batch_write_item": "dynamodb:BatchWriteItem",
    "batch_writer": "dynamodb:BatchWriteItem",
    "transact_write_items": "dynamodb:TransactWriteItems",
}


class _CfnLoader(yaml.SafeLoader):
    """Reads !GetAtt, !Ref, !Sub ... as plain values."""


def _any_tag(loader, _suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _any_tag)


class _Module:
    def __init__(self, name):
        self.name = name
        tree = ast.parse(open(os.path.join(SRC, name + ".py")).read())
        self.aliases = {}   # local name -> src module name
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if os.path.exists(os.path.join(SRC, a.name + ".py")):
                        self.aliases[a.asname or a.name] = a.name
        self.functions = {n.name: n for n in ast.walk(tree)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


_modules = {}


def _module(name):
    if name not in _modules:
        _modules[name] = _Module(name)
    return _modules[name]


def reachable_actions(module_name, function_name):
    """IAM actions for every DynamoDB call reachable from module.function."""
    actions, seen, todo = set(), set(), [(module_name, function_name)]
    while todo:
        mod_name, fn_name = todo.pop()
        if (mod_name, fn_name) in seen:
            continue
        seen.add((mod_name, fn_name))
        mod = _module(mod_name)
        fn = mod.functions.get(fn_name)
        if fn is None:
            continue
        for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
            f = call.func
            if isinstance(f, ast.Name):
                todo.append((mod_name, f.id))
            elif isinstance(f, ast.Attribute):
                if f.attr in DDB_ACTIONS:
                    actions.add(DDB_ACTIONS[f.attr])
                if isinstance(f.value, ast.Name) and f.value.id in mod.aliases:
                    todo.append((mod.aliases[f.value.id], f.attr))
            # Functions passed as values (e.g. functools.partial(save_message, ...)).
            for arg in call.args:
                if isinstance(arg, ast.Name):
                    todo.append((mod_name, arg.id))
    return actions


def _policy_actions(props):
    allowed = set()
    for policy in props.get("Policies") or []:
        if not isinstance(policy, dict):
            continue
        for stmt in policy.get("Statement") or []:
            if stmt.get("Effect") != "Allow":
                continue
            acts = stmt.get("Action")
            allowed.update([acts] if isinstance(acts, str) else acts or [])
    return allowed


def _ddb_functions():
    template = yaml.load(open(TEMPLATE), Loader=_CfnLoader)
    out = []
    for name, res in template["Resources"].items():
        if res.get("Type") != "AWS::Serverless::Function":
            continue
        props = res["Properties"]
        if props.get("CodeUri", "").rstrip("/") != "src":
            continue
        module, handler = props["Handler"].rsplit(".", 1)
        if not os.path.exists(os.path.join(SRC, module + ".py")):
            continue
        out.append((name, module, handler, _policy_actions(props)))
    return out


def _allows(allowed, action):
    return any(re.fullmatch(a.replace("*", ".*"), action) for a in allowed)


def test_every_reachable_dynamodb_call_is_allowed():
    missing = {}
    for name, module, handler, allowed in _ddb_functions():
        gaps = sorted(a for a in reachable_actions(module, handler) if not _allows(allowed, a))
        if gaps:
            missing[name] = gaps
    assert not missing, f"Lambda policies missing DynamoDB actions their code calls: {missing}"


def test_the_flight_confirmation_handlers_may_delete():
    """The case that broke live: taking a held flight deletes it."""
    by_name = {n: (m, h, a) for n, m, h, a in _ddb_functions()}
    for fn in ("ChatMessageFunction", "GroupMessageFunction"):
        module, handler, allowed = by_name[fn]
        assert "dynamodb:DeleteItem" in reachable_actions(module, handler), fn
        assert _allows(allowed, "dynamodb:DeleteItem"), fn
