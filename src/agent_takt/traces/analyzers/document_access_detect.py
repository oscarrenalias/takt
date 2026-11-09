"""Raw spec/ADR access detection over ``tool_call`` trace events.

Pure parsing: this module only extracts access *candidates*
``{call_id, method, doc_type, target, query}``. It never resolves a target to
a document id and never touches the filesystem.

Methods: ``cli_show``, ``cli_search``, ``cli_list``, ``read``, ``shell_read``,
``grep``.
"""
from __future__ import annotations

import posixpath
import shlex
from pathlib import Path
from typing import Any, Iterable

METHOD_CLI_SHOW = "cli_show"
METHOD_CLI_SEARCH = "cli_search"
METHOD_CLI_LIST = "cli_list"
METHOD_READ = "read"
METHOD_SHELL_READ = "shell_read"
METHOD_GREP = "grep"

_DOC_DIRS = {"specs": "spec", "adr": "adr"}
_CLI_VERBS = {"show": METHOD_CLI_SHOW, "search": METHOD_CLI_SEARCH, "list": METHOD_CLI_LIST}
_TAKT_NAMES = {"takt", "$TAKT_CMD", "${TAKT_CMD}"}
_SEPARATORS = {"&&", "||", ";", "|", "&", "|&", ";;"}
# CLI options (spec/adr show|search|list) that consume a following value.
_VALUE_FLAGS = {"--field", "--status", "--tag", "--limit", "--related-spec"}
_READ_COMMANDS = {"cat", "head", "tail", "sed", "grep", "rg", "less"}
_PATTERN_COMMANDS = {"grep", "rg", "sed"}


def _split_segments(command: str) -> list[list[str]]:
    """Split a command line into token lists on ``&&``, ``||``, ``;``, ``|`` (quote aware)."""
    segments: list[list[str]] = []
    for line in command.splitlines():
        if not line.strip():
            continue
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            tokens = line.split()
            # crude fallback: separate glued separators are not handled
        current: list[str] = []
        for tok in tokens:
            if tok in _SEPARATORS:
                if current:
                    segments.append(current)
                current = []
            elif tok and set(tok) <= set("&|;") and len(tok) > 1:
                if current:
                    segments.append(current)
                current = []
            else:
                current.append(tok)
        if current:
            segments.append(current)
    return segments


def _strip_env(tokens: list[str]) -> list[str]:
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        name, eq, _ = tok.partition("=")
        if eq and name and name.replace("_", "a").isalnum() and not name[0].isdigit():
            i += 1
        else:
            break
    return tokens[i:]


def _is_takt(token: str) -> bool:
    return token in _TAKT_NAMES or (token.startswith("/") and token.endswith("/takt"))


def _after_takt_prefix(tokens: list[str]) -> list[str] | None:
    """Return the tokens after a recognised takt prefix, else ``None``."""
    if not tokens:
        return None
    if _is_takt(tokens[0]):
        return tokens[1:]
    if tokens[0] == "uv" and len(tokens) > 1 and tokens[1] == "run":
        i = 2
        while i < len(tokens):
            tok = tokens[i]
            if _is_takt(tok):
                return tokens[i + 1:]
            if not tok.startswith("-"):
                return None
            if "=" not in tok and tok in {"--directory", "--project", "--with", "--python", "--env-file", "--package"}:
                i += 2
            else:
                i += 1
    return None


def _positionals(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-") and len(tok) > 1:
            if "=" not in tok and tok in _VALUE_FLAGS:
                i += 1
        else:
            out.append(tok)
        i += 1
    return out


def _cli_candidates(call_id: Any, rest: list[str]) -> list[dict[str, Any]]:
    # Tolerate global options before the document group.
    while rest and rest[0].startswith("-"):
        rest = rest[1:]
    if len(rest) < 2 or rest[0] not in ("spec", "adr") or rest[1] not in _CLI_VERBS:
        return []
    doc_type, method = rest[0], _CLI_VERBS[rest[1]]
    args = _positionals(rest[2:])
    if method == METHOD_CLI_SHOW:
        return [_candidate(call_id, method, doc_type, a, None) for a in args]
    if method == METHOD_CLI_SEARCH:
        query = " ".join(args) if args else None
        return [_candidate(call_id, method, doc_type, None, query)]
    return [_candidate(call_id, method, doc_type, None, None)]


def _candidate(call_id: Any, method: str, doc_type: str | None, target: str | None,
               query: str | None) -> dict[str, Any]:
    return {"call_id": call_id, "method": method, "doc_type": doc_type,
            "target": target, "query": query}


def relativize_doc_path(path: Any, cwd: str | None, main_root: str | Path | None) -> tuple[str, str] | None:
    """Return ``(doc_type, relative_path)`` if ``path`` is under ``specs/`` or ``adr/``.

    Absolute paths are made relative to ``cwd`` first, then to ``main_root``;
    paths outside both are ignored. Relative paths are taken as given.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    path = path.strip()
    if path.startswith("~"):
        return None
    if posixpath.isabs(path):
        rel: str | None = None
        for base in (cwd, str(main_root) if main_root else None):
            if not base:
                continue
            base_n = posixpath.normpath(base)
            cand = posixpath.normpath(path)
            if cand == base_n:
                continue
            prefix = base_n.rstrip("/") + "/"
            if cand.startswith(prefix):
                candidate_rel = cand[len(prefix):]
                if candidate_rel.split("/", 1)[0] in _DOC_DIRS:
                    rel = candidate_rel
                    break
                if rel is None:
                    rel = candidate_rel
        if rel is None:
            return None
    else:
        rel = posixpath.normpath(path)
    if rel.startswith("../") or rel == "..":
        return None
    first = rel.split("/", 1)[0]
    doc_type = _DOC_DIRS.get(first)
    if doc_type is None:
        return None
    return doc_type, rel


def _shell_read_candidates(call_id: Any, tokens: list[str], cwd: str | None,
                           main_root: str | Path | None) -> list[dict[str, Any]]:
    cmd = posixpath.basename(tokens[0]) if tokens else ""
    if cmd not in _READ_COMMANDS:
        return []
    args = tokens[1:]
    if cmd == "sed" and "-n" not in args and not any(a.startswith("-n") for a in args):
        return []
    explicit_pattern = any(a in ("-e", "-f", "--regexp", "--file") or a.startswith("-e") and cmd == "sed"
                           for a in args)
    positionals = [a for a in args if not (a.startswith("-") and len(a) > 1)]
    if cmd in _PATTERN_COMMANDS and not explicit_pattern and positionals:
        positionals = positionals[1:]
    out: list[dict[str, Any]] = []
    for arg in positionals:
        hit = relativize_doc_path(arg, cwd, main_root)
        if hit:
            out.append(_candidate(call_id, METHOD_SHELL_READ, hit[0], hit[1], None))
    return out


def _session_cwd(events: Iterable[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("kind") == "session" and event.get("cwd"):
            return str(event["cwd"])
    return None


def detect_document_accesses(
    events: Iterable[dict[str, Any]],
    main_checkout_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return raw access candidates for every spec/ADR access in ``events``."""
    events = list(events)
    cwd = _session_cwd(events)
    found: list[dict[str, Any]] = []
    for event in events:
        if event.get("kind") != "tool_call":
            continue
        tool = event.get("tool")
        payload = event.get("input")
        if not isinstance(payload, dict):
            continue
        call_id = event.get("call_id")
        if tool == "Bash":
            command = payload.get("command")
            if not isinstance(command, str):
                continue
            for tokens in _split_segments(command):
                tokens = _strip_env(tokens)
                rest = _after_takt_prefix(tokens)
                if rest is not None:
                    found.extend(_cli_candidates(call_id, rest))
                else:
                    found.extend(_shell_read_candidates(call_id, tokens, cwd, main_checkout_root))
        elif tool == "Read":
            hit = relativize_doc_path(payload.get("file_path") or payload.get("path"), cwd, main_checkout_root)
            if hit:
                found.append(_candidate(call_id, METHOD_READ, hit[0], hit[1], None))
        elif tool in ("Grep", "Glob"):
            query = payload.get("pattern") if isinstance(payload.get("pattern"), str) else None
            hit = relativize_doc_path(payload.get("path"), cwd, main_checkout_root)
            if hit is None and tool == "Glob" and query and not posixpath.isabs(query):
                hit = relativize_doc_path(query, cwd, main_checkout_root)
            if hit:
                found.append(_candidate(call_id, METHOD_GREP, hit[0], hit[1], query))
    return found
