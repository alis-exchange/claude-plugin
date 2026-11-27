#!/usr/bin/env python3
"""Take a service's defined packages into its code.

    install_packages.py <package id> [--version <version>]
    install_packages.py <package id> --add
    install_packages.py <package id> --upgrade-all
    install_packages.py <package id> --auth-only

Run it after a define has finished. It finds every manifest in the service's
folder (go.mod, package.json, requirements.txt, pubspec.yaml), signs the
machine in to the product's private package registries, and runs the commands
AlisX generates for each: an install, then the service's own defined package
at its newest version, or at exactly --version.

Never write registry settings or version pins by hand (.netrc, .npmrc, a
`replace` in go.mod, a sed over a manifest): this script does both, and a
hand-made one goes stale within the hour.

Go, Node, Python and Dart are set up here. .NET, Swift, Rust, Java and Kotlin
need registry files this script does not write yet: it says so and leaves
them alone, and the AlisX CLI (`alis packages install`) handles them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _alisx as alisx  # noqa: E402

MANIFESTS = {
    "go.mod": "GO",
    "package.json": "NODE",
    "requirements.txt": "PYTHON",
    "pubspec.yaml": "DART",
    "Package.swift": "SWIFT",
    "Cargo.toml": "RUST",
    "pom.xml": "JAVA",
    "build.gradle": "KOTLIN",
    "build.gradle.kts": "KOTLIN",
}
SET_UP_HERE = ("GO", "NODE", "PYTHON", "DART")
LABELS = {"GO": "Go", "NODE": "Node", "PYTHON": "Python", "DART": "Dart", "DOTNET": ".NET",
          "SWIFT": "Swift", "RUST": "Rust", "JAVA": "Java", "KOTLIN": "Kotlin"}
ALIASES = {"go": "GO", "golang": "GO", "node": "NODE", "npm": "NODE", "pnpm": "NODE", "js": "NODE",
           "javascript": "NODE", "python": "PYTHON", "py": "PYTHON", "dart": "DART", "flutter": "DART"}
SKIPPED_FOLDERS = {".git", "vendor", "node_modules", ".venv", ".dart_tool", ".build", "target", ".gradle", ".alis"}


# --- which manifests there are, and whether each takes the service's package --


def language_of(path: Path) -> str:
    if path.suffix in (".csproj", ".fsproj"):
        return "DOTNET"
    return MANIFESTS.get(path.name, "")


def declares_own_package(manifest: Path, language: str, package: str) -> bool:
    """Whether a manifest names the service's own defined package (alis.build/...)."""
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if language == "GO":
        module = "alis.build/" + package.replace(".", "/")
        if module.endswith("/v1"):
            module = module[: -len("/v1")]
        return re.search(r"(?m)^\s*(?:require\s+)?%s\s+v\d" % re.escape(module), text) is not None
    if language == "NODE":
        return '"@alis.build/%s"' % package.replace(".", "-") in text
    if language == "PYTHON":
        wanted = re.sub(r"[-_.]+", "-", package).lower()
        for line in text.splitlines():
            name = re.split(r"[\s<>=!~;\[#]", line.strip(), 1)[0]
            if name and re.sub(r"[-_.]+", "-", name).lower() == wanted:
                return True
        return False
    if language == "DART":
        name = package.replace(".", "_").replace("-", "_").lower()
        return re.search(r"(?m)^\s+%s\s*:" % re.escape(name), text) is not None
    return False


def find_locations(folder: Path, repo: Path, package: str, only: str, paths: list) -> list:
    wanted_dirs = [(folder / p).resolve() for p in paths]
    locations = []
    for directory, subfolders, files in os.walk(str(folder)):
        subfolders[:] = sorted(name for name in subfolders if name not in SKIPPED_FOLDERS)
        here = Path(directory)
        if wanted_dirs and here.resolve() not in wanted_dirs:
            continue
        for name in sorted(files):
            manifest = here / name
            language = language_of(manifest)
            if not language or (only and language != only):
                continue
            locations.append({
                "workingDirectory": str(here),
                "language": language,
                "buildDirectory": str(repo),
                "usesScopedDefinedPackage": declares_own_package(manifest, language, package),
                "manifestPath": str(manifest),
            })
    return locations


# --- the machine's sign-in to the product's registries -----------------------


def edit_lines(path: Path, change) -> None:
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    lines = existing[:-1].split("\n") if existing.endswith("\n") else (existing.split("\n") if existing else [])
    content = "\n".join(change(lines)) + "\n"
    if content != existing:
        alisx.write_private(path, content)


def set_netrc(lines: list, hosts: list, token: str) -> list:
    """Replace each host's entry in place, or add it; keep everything else."""
    for host in hosts:
        entry = ["machine " + host, "login oauth2accesstoken", "password " + token]
        start = end = -1
        for index, line in enumerate(lines):
            fields = line.split()
            if not fields:
                continue
            starts_entry = fields[0] in ("machine", "default")
            if start >= 0 and starts_entry:
                end = index
                break
            if start < 0 and fields[0] == "machine" and len(fields) > 1 and fields[1] == host:
                start = index
        if start < 0:
            if lines and lines[-1].strip():
                lines.append("")
            lines += entry
            continue
        if end < 0:
            end = len(lines)
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines[start:end] = entry
    return lines


def set_npmrc(lines: list, hosts: list, token: str) -> list:
    for host in hosts:
        key = "//%s:_authToken" % host
        replaced = False
        for index, line in enumerate(lines):
            name, separator, _ = line.strip().partition("=")
            if separator and name.strip() == key:
                lines[index] = "" if replaced else "%s=%s" % (key, token)
                replaced = True
        if not replaced:
            lines.append("%s=%s" % (key, token))
    return lines


def pub_tokens_path() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "dart" / "pub-tokens.json"


def set_pub_tokens(hosts: dict) -> None:
    path = pub_tokens_path()
    try:
        tokens = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tokens = {}
    hosted = {entry.get("url"): entry for entry in tokens.get("hosted") or [] if isinstance(entry, dict)}
    for url, token in hosts.items():
        hosted[url] = {"url": url, "token": token}
    alisx.write_private(path, json.dumps({"hosted": list(hosted.values()), "version": tokens.get("version") or 1}) + "\n")


def sign_in_to_registries(product: str, languages: set) -> list:
    """Write the registries' credentials where each package manager looks for them."""
    answer = alisx.api("ProductsService", "GenerateRegistryCredentials", {"product": product})
    token = answer.get("accessToken") or ""
    if not token:
        raise alisx.Stop("AlisX answered with no registry credentials for %s, so nothing was installed." % product)
    netrc, npm, dart = [], [], {}
    for host in answer.get("hosts", []):
        registry, address = host.get("registry"), host.get("host") or ""
        name = urllib.parse.urlparse(address if "://" in address else "https://" + address).hostname or ""
        if registry in ("GO", "PYTHON", "DOCKER") and name and name not in netrc:
            netrc.append(name)
        elif registry == "NPM" and address and address not in npm:
            npm.append(address)
        elif registry == "DART" and address and host.get("token"):
            dart[address] = host["token"]
    written = []
    if netrc and languages & {"GO", "PYTHON"}:
        edit_lines(Path.home() / ".netrc", lambda lines: set_netrc(lines, netrc, token))
        written.append("~/.netrc")
    if npm and "NODE" in languages:
        edit_lines(Path.home() / ".npmrc", lambda lines: set_npmrc(lines, npm, token))
        written.append("~/.npmrc")
    if dart and "DART" in languages:
        set_pub_tokens(dart)
        written.append(str(pub_tokens_path()))
    return written


# --- running what AlisX generated --------------------------------------------


def platform() -> str:
    if sys.platform.startswith("win"):
        return "WINDOWS"
    return "MACOS" if sys.platform == "darwin" else "LINUX"


def run_command(command: str, folder: str) -> int:
    if sys.platform.startswith("win"):
        argv = ["powershell", "-NoProfile", "-Command", command]
    else:
        argv = ["sh", "-c", command]
    return subprocess.run(argv, cwd=folder).returncode


def commands_for(script: dict, action: str, own: bool) -> list:
    if action == "add":
        chosen = [script.get("addDefined")]
    elif action == "upgrade-all":
        chosen = [script.get("upgradePackages")]
    elif own:
        # Install first: a fresh clone needs its lock file in place before the
        # one package is fetched at its new version.
        chosen = [script.get("installPackages"), script.get("upgradeDefined")]
    else:
        chosen = [script.get("installPackages")]
    return [command for command in chosen if command and command.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Take a service's defined packages into its code.")
    parser.add_argument("package", help="the service's package id, for example alis.os.users.v1")
    parser.add_argument("--version", default="", help="the defined version to take, for example 1.4.0 (default: the newest)")
    parser.add_argument("--add", action="store_true", help="add the service's defined package to manifests that do not have it yet")
    parser.add_argument("--upgrade-all", action="store_true", help="upgrade every package, third-party ones too")
    parser.add_argument("--language", default="", help="only this language: go, node, python or dart")
    parser.add_argument("--path", action="append", default=[], help="only the manifest in this folder, relative to the service's folder (repeatable; . is its root)")
    parser.add_argument("--auth-only", action="store_true", help="only sign this machine in to the product's package registries: no install, and no version changes")
    args = parser.parse_args()
    if sum((args.add, args.upgrade_all, args.auth_only)) > 1:
        parser.error("--add, --upgrade-all and --auth-only are each a run of their own")
    action = "add" if args.add else "upgrade-all" if args.upgrade_all else "install"

    only = ""
    if args.language:
        only = ALIASES.get(args.language.lower(), "")
        if not only:
            raise alisx.Stop("%r is not a language this script sets up: go, node, python or dart." % args.language, alisx.EXIT_USAGE)

    service = alisx.Service(args.package)
    repo = alisx.build_root(service.organisation, service.product)
    folder = service.build_folder
    if not folder.is_dir():
        raise alisx.Stop(
            "The service's folder %s is not on this machine. Clone the product first: "
            "git.py clone %s.%s" % (folder, service.organisation, service.product)
        )

    found = find_locations(folder, repo, service.package, only, args.path)
    locations = [location for location in found if location["language"] in SET_UP_HERE]
    left = sorted({LABELS[location["language"]] for location in found if location["language"] not in SET_UP_HERE})
    if left:
        alisx.say("Left alone (%s): this script does not set up their registries yet. With the AlisX CLI: alis packages install %s" % (", ".join(left), service.package))
    if not locations:
        raise alisx.Stop(
            "No go.mod, package.json, requirements.txt or pubspec.yaml was found under %s%s, "
            "so there was nothing to install." % (folder, " for that language or path" if only or args.path else "")
        )

    languages = {location["language"] for location in locations}
    if args.auth_only:
        written = sign_in_to_registries(service.product_name, languages)
        alisx.say("Signed this machine in to the product's package registries (%s), for about an hour. Nothing was installed and no version changed."
                  % (", ".join(written) or "none were needed"))
        if "GO" in languages:
            alisx.say("Go also reads GOPROXY and GONOSUMDB, which the environment's .env from fetch_envs.py holds: run.py loads both for you.")
        return 0
    for location in locations:
        if location["language"] == "PYTHON":
            location["pythonPackageManager"] = "UV" if shutil.which("uv") else "PIP"

    written = sign_in_to_registries(service.product_name, languages)
    if written:
        alisx.say("Signed this machine in to the product's package registries (%s), for about an hour." % ", ".join(written))

    answer = alisx.api("NeuronsService", "GeneratePackageScripts", {
        "neuron": alisx.service_name(service),
        "locations": locations,
        "targetPlatform": platform(),
        "definedVersion": args.version.lstrip("v"),
    })
    own = {(location["workingDirectory"], location["language"]): location["usesScopedDefinedPackage"] for location in locations}

    failed, done = [], 0
    for script in answer.get("scripts", []):
        directory, language = script.get("workingDirectory") or "", script.get("language") or ""
        where = "%s in %s" % (LABELS.get(language, language), os.path.relpath(directory, str(folder)) if directory else "?")
        commands = commands_for(script, action, own.get((directory, language), False))
        if not commands:
            alisx.say("%s: nothing to run for this (%s)." % (where, action))
            continue
        for command in commands:
            alisx.say("%s: %s" % (where, command))
            if run_command(command, directory) != 0:
                failed.append(where)
                break
        else:
            done += 1
        if action == "install" and not own.get((directory, language), False):
            alisx.say("%s does not declare the service's own defined package yet: run this again with --add to add it." % where)

    if failed:
        raise alisx.Stop(
            "Failed in: %s. Read the package manager's own output above; the manifests "
            "may be half changed (see `git status`). A version that was only just "
            "defined may still be generating: wait for the define with the connector's `wait`, "
            "then run this again." % "; ".join(failed)
        )
    alisx.say("Done: %d location(s). Next: build and test, commit, `git.py push`, then the connector's `build`." % done)
    return 0


if __name__ == "__main__":
    alisx.run(main)
