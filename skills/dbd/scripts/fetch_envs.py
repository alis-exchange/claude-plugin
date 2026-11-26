#!/usr/bin/env python3
"""Write a deployment environment's variables to a local env file.

    fetch_envs.py <build environment>.<product> <environment> [--production]

It only reads: nothing about the environment changes. Changing a variable is
the connector's work (`update`, part variables), never this script's.

The values are written to the product's clone, where git ignores them:

    <product's clone>/.alis/.env        the variables
    <product's clone>/.alis/key.json    the environment's service account key

This prints the variables' names, never their values. Do not print or read
the two files into the conversation: load them into the command that needs
them with the line this script ends on.
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _alisx as alisx  # noqa: E402


def find_environment(organisation: str, product: str, wanted: str) -> dict:
    parent = alisx.product_name(organisation, product)
    environments, page = [], ""
    while True:
        answer = alisx.api("EnvironmentsService", "ListEnvironments", {"parent": parent, "pageSize": 100, "pageToken": page})
        environments += answer.get("environments", [])
        page = answer.get("nextPageToken") or ""
        if not page:
            break
    lowered = wanted.strip().lower()
    for environment in environments:
        identifier = environment.get("name", "").rsplit("/", 1)[-1]
        if lowered in (identifier.lower(), environment.get("displayName", "").lower(), environment.get("name", "").lower()):
            return environment
    known = ", ".join(
        "%s (%s)" % (e.get("displayName") or "unnamed", e.get("name", "").rsplit("/", 1)[-1]) for e in environments
    )
    raise alisx.Stop(
        "The product %s.%s has no deployment environment %r. It has: %s."
        % (organisation, product, wanted, known or "none")
    )


def ignore(repo: Path) -> None:
    """Keep the folder that holds the values out of git."""
    if not (repo / ".git").exists():
        return
    path = repo / ".gitignore"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    if any(line.strip() in ("/.alis/", ".alis/", ".alis", "/.alis") for line in lines):
        return
    with path.open("a", encoding="utf-8") as handle:
        if lines and lines[-1].strip():
            handle.write("\n")
        handle.write("/.alis/\n")


def names_in(dotenv: str) -> list:
    names = []
    for line in dotenv.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            names.append(line.split("=", 1)[0].replace("export ", "").strip())
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a deployment environment's variables to a local env file.")
    parser.add_argument("product", help="<build environment>.<product>, for example alis.os")
    parser.add_argument("environment", help="the deployment environment, by name or id, for example Development")
    parser.add_argument("--production", action="store_true",
                        help="pass only when the person said, in this conversation, to fetch a production environment's values")
    parser.add_argument("--output", help="the folder to write .env and key.json in (default: .alis in the product's clone)")
    args = parser.parse_args()

    organisation, product = alisx.split_product(args.product)
    environment = find_environment(organisation, product, args.environment)
    label = environment.get("displayName") or environment["name"].rsplit("/", 1)[-1]
    if environment.get("type") == "PROD" and not args.production:
        raise alisx.Stop(
            "%s is a production environment. Its values and service account key are "
            "written to this machine only on the person's own word: ask them, and run "
            "this again with --production if they say so. Nothing was fetched." % label
        )

    repo = alisx.build_root(organisation, product)
    folder = Path(args.output).expanduser().resolve() if args.output else repo / ".alis"
    if not args.output and not repo.exists():
        raise alisx.Stop(
            "The product is not cloned at %s. Run `git.py clone %s` first, or name a folder with --output."
            % (repo, args.product)
        )
    env_path, key_path = folder / ".env", folder / "key.json"

    answer = alisx.api(
        "EnvironmentsService",
        "GenerateEnvironmentCredentials",
        {"environment": environment["name"], "credentialsPath": str(key_path)},
    )
    dotenv = base64.b64decode(answer.get("dotenv") or "").decode("utf-8")
    key = base64.b64decode(answer.get("serviceAccountKey") or "")
    if not dotenv:
        raise alisx.Stop("AlisX answered with no variables for %s, so nothing was written." % label)

    alisx.write_private(env_path, dotenv if dotenv.endswith("\n") else dotenv + "\n")
    if key:
        alisx.write_private(key_path, key.decode("utf-8"))
    if not args.output:
        ignore(repo)

    names = names_in(dotenv)
    alisx.say("Wrote %d variable(s) of %s to %s (only you can read it; git ignores it)." % (len(names), label, env_path))
    if key:
        alisx.say("Wrote its service account key to %s." % key_path)
    alisx.say("Names: " + ", ".join(names))
    alisx.say("Never print these files or their values. To run a command with them:")
    alisx.say('  set -a; . "%s"; set +a; <command>' % env_path)
    return 0


if __name__ == "__main__":
    alisx.run(main)
