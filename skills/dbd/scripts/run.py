#!/usr/bin/env python3
"""Run a service on this machine against a deployment environment.

    run.py <package id> <environment> [--production] [--fresh] [-- <command> ...]

It does what running a service by hand takes, in one step:

1. writes the environment's variables and service account key to the
   product's clone (`.alis/.env`, `.alis/key.json`), as fetch_envs.py does,
   and reuses them for 12 hours unless --fresh;
2. signs the machine in to the product's package registries, as
   install_packages.py --auth-only does (no install, no version changes);
3. runs the service in its folder with those variables: `go run .` for a
   Go service, `npm start` for a Node one, or the command after `--`.

The variables' values are never printed. A production environment's values
come to this machine only with --production, on the person's own word.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _alisx as alisx  # noqa: E402
import fetch_envs  # noqa: E402
import install_packages  # noqa: E402

# How long fetched values are used again before they are fetched anew.
REUSE_FOR = 12 * 3600

DEFAULT_COMMANDS = {"GO": ["go", "run", "."], "NODE": ["npm", "start"]}


def languages_of(folder: Path) -> set:
    found = set()
    for name, language in (("go.mod", "GO"), ("package.json", "NODE"), ("requirements.txt", "PYTHON"),
                           ("pyproject.toml", "PYTHON"), ("pubspec.yaml", "DART")):
        if (folder / name).exists():
            found.add(language)
    return found


def read_dotenv(path: Path) -> dict:
    """The variables of an env file, as `set -a; . file` would set them."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.replace("export ", "", 1).strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                value = json.loads(value)
            except ValueError:
                value = value[1:-1]
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[name] = value
    return values


def environment_values(service: alisx.Service, wanted: str, production: bool, fresh: bool) -> tuple:
    """The env file to run with, fetched now or kept from a fetch of the same environment."""
    repo = alisx.build_root(service.organisation, service.product)
    folder = repo / ".alis"
    env_path, marker = folder / ".env", folder / "environment.json"
    try:
        kept = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    reusable = (not fresh and env_path.exists() and kept.get("asked") == wanted.strip().lower()
                and time.time() - kept.get("at", 0) < REUSE_FOR and (production or not kept.get("production")))
    if reusable:
        return env_path, kept.get("label") or wanted, False
    env_path, _, _, label = fetch_envs.fetch("%s.%s" % (service.organisation, service.product), wanted, production)
    alisx.write_private(marker, json.dumps({"asked": wanted.strip().lower(), "label": label, "at": time.time(), "production": production}))
    return env_path, label, True


def main() -> int:
    argv = sys.argv[1:]
    command = []
    if "--" in argv:
        at = argv.index("--")
        argv, command = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(description="Run a service on this machine against a deployment environment.",
                                     usage="run.py <package id> <environment> [--production] [--fresh] [-- <command> ...]")
    parser.add_argument("package", help="the service's package id, for example alis.os.users.v1")
    parser.add_argument("environment", help="the deployment environment, by name or id, for example Development")
    parser.add_argument("--production", action="store_true",
                        help="pass only when the person said, in this conversation, to run against a production environment")
    parser.add_argument("--fresh", action="store_true", help="fetch the environment's values again, even if a fetch of the last 12 hours is kept")
    args = parser.parse_args(argv)

    service = alisx.Service(args.package)
    folder = service.build_folder
    if not folder.is_dir():
        raise alisx.Stop(
            "The service's folder %s is not on this machine. Clone the product first: "
            "git.py clone %s.%s" % (folder, service.organisation, service.product)
        )
    languages = languages_of(folder)
    if not command:
        for language in ("GO", "NODE"):
            if language in languages:
                command = DEFAULT_COMMANDS[language]
                break
    if not command:
        raise alisx.Stop(
            "There is no go.mod or package.json in %s, so there is no usual way to run it: "
            "name the command after --, for example run.py %s %s -- python3 main.py"
            % (folder, args.package, args.environment), alisx.EXIT_USAGE
        )

    env_path, label, fetched = environment_values(service, args.environment, args.production, args.fresh)
    values = read_dotenv(env_path)
    alisx.say("%s %d variable(s) of %s (%s)." % ("Fetched" if fetched else "Using the", len(values), label, env_path))

    signed = install_packages.sign_in_to_registries(service.product_name, languages & set(install_packages.SET_UP_HERE))
    if signed:
        alisx.say("Signed this machine in to the product's package registries (%s)." % ", ".join(signed))

    env = dict(os.environ)
    env.update(values)
    alisx.say("Running in %s: %s" % (folder, " ".join(command)))
    try:
        done = subprocess.run(command, cwd=str(folder), env=env)
    except FileNotFoundError:
        raise alisx.Stop("%s is not installed on this machine. Install it and run this again." % command[0])
    return done.returncode


if __name__ == "__main__":
    alisx.run(main)
