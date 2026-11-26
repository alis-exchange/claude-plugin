#!/usr/bin/env python3
"""Clone, pull and push a product's repos, signed in as the person.

    git.py clone <build environment>.<product>
    git.py pull [<build environment>.<product>]
    git.py push

A build environment has one definitions repo (`proto`) and one repo for each
product. They are cloned where the AlisX CLI clones them, so both see one tree:

    ~/alis.build/<build environment>/define            the definitions
    ~/alis.build/<build environment>/build/<product>   the product's code

Plain `git clone`, `git pull` and `git push` have no sign-in for these repos:
this script hands git one for the single command it runs, and stores none.
Everything that stays on the machine (status, add, commit, branch, diff, log)
is plain git.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _alisx as alisx  # noqa: E402


def is_repo(folder: Path) -> bool:
    return (folder / ".git").exists()


def top(folder: Path) -> Path:
    try:
        return Path(alisx.git(folder, "rev-parse", "--show-toplevel").strip())
    except alisx.GitFailed:
        raise alisx.Stop(
            "%s is not inside a git repo. Run this from a product's clone, or clone it "
            "first with: git.py clone <build environment>.<product>" % folder
        )


def branch_of(repo: Path) -> str:
    try:
        return alisx.git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    except alisx.GitFailed:
        raise alisx.Stop(
            "%s is not on a branch (a detached checkout). Check out a branch first: "
            "git checkout <branch>" % repo
        )


def kind_of(repo: Path) -> str:
    """Whether a repo is a build environment's definitions or a product's code."""
    try:
        url = alisx.git(repo, "remote", "get-url", "origin").strip()
    except alisx.GitFailed:
        return ""
    return "definitions" if url.rstrip("/").endswith(("/proto.git", "/proto")) else "product"


def set_up(repo: Path) -> None:
    """What a fresh clone needs before the first commit, where it is missing."""
    # Large files, where the repo keeps some and git-lfs is there.
    if alisx.git_ok(repo, "lfs", "version"):
        alisx.git_ok(repo, "lfs", "install", "--local")
    # An author for commits, when the machine has none set.
    if alisx.git_ok(repo, "config", "user.email"):
        return
    try:
        me = alisx.api("IdentityService", "RetrieveMe")
    except alisx.Stop:
        return
    if me.get("email"):
        alisx.git_ok(repo, "config", "user.email", me["email"])
        alisx.git_ok(repo, "config", "user.name", me.get("displayName") or me["email"])


def pull_repo(repo: Path) -> str:
    branch = branch_of(repo)
    try:
        alisx.git(repo, "-c", "pull.rebase=false", "pull", "--ff-only", "origin", branch, remote=True)
    except alisx.GitFailed as failed:
        said = failed.output.lower()
        if "couldn't find remote ref" in said or "could not find remote ref" in said:
            return "%s: %s is not on the remote yet, so there is nothing to pull." % (repo, branch)
        if "not possible to fast-forward" in said or "diverg" in said or "overwritten" in said:
            raise alisx.Stop(
                "%s could not be brought up to date: %s and its remote have each moved, "
                "or local changes are in the way. Nothing was changed. Commit or stash, "
                "then merge or rebase origin/%s by hand.\n%s" % (repo, branch, branch, failed.output.strip())
            )
        raise
    return "%s: %s is up to date with its remote, at %s." % (repo, branch, head(repo))


def head(repo: Path) -> str:
    return alisx.git(repo, "log", "-1", "--format=%h %s").strip()


def clone_one(url: str, folder: Path, what: str) -> None:
    if is_repo(folder):
        alisx.say("Already cloned, pulling. " + pull_repo(folder))
        return
    if folder.exists() and any(folder.iterdir()):
        raise alisx.Stop(
            "%s exists and is not a git repo, so %s was not cloned there. Move it away "
            "and run this again." % (folder, what)
        )
    folder.parent.mkdir(parents=True, exist_ok=True)
    alisx.git(folder.parent, "clone", url, str(folder), remote=True)
    set_up(folder)
    alisx.say("Cloned %s to %s." % (what, folder))


def clone(product: str) -> int:
    organisation, product_id = alisx.split_product(product)
    host = alisx.git_host(organisation)
    definitions = alisx.define_root(organisation)
    code = alisx.build_root(organisation, product_id)
    clone_one(host + "/proto.git", definitions, "the definitions of %s" % organisation)
    clone_one(host + "/%s.git" % product_id, code, "the product %s.%s" % (organisation, product_id))
    alisx.say()
    alisx.say("A service <build environment>.<product>.<service>.v<N> has its code in")
    alisx.say("  %s/<service>/v<N>   (its Terraform in infra/ there)" % code)
    alisx.say("and its protos in")
    alisx.say("  %s/%s/%s/<service>/v<N>" % (definitions, organisation, product_id))
    return 0


def pull(product: str | None) -> int:
    if product:
        organisation, product_id = alisx.split_product(product)
        repos = [alisx.define_root(organisation), alisx.build_root(organisation, product_id)]
        missing = [str(repo) for repo in repos if not is_repo(repo)]
        if missing:
            raise alisx.Stop("Not cloned yet: %s. Run: git.py clone %s" % (", ".join(missing), product))
    else:
        repos = [top(Path.cwd())]
    for repo in repos:
        alisx.say(pull_repo(repo))
    return 0


def push() -> int:
    repo = top(Path.cwd())
    branch = branch_of(repo)
    try:
        alisx.git(repo, "push", "--set-upstream", "origin", "HEAD:refs/heads/" + branch, remote=True)
    except alisx.GitFailed as failed:
        said = failed.output.lower()
        if "non-fast-forward" in said or "fetch first" in said or "failed to push some refs" in said:
            raise alisx.Stop(
                "The remote %s has commits this checkout lacks, so nothing was pushed "
                "and nothing was forced. Run `git.py pull`, sort out what it reports, "
                "then push again.\n%s" % (branch, failed.output.strip())
            )
        raise
    commit = alisx.git(repo, "rev-parse", "HEAD").strip()
    alisx.say("Pushed %s at %s to its remote." % (branch, head(repo)))
    alisx.say("Commit: %s" % commit)
    left = [line for line in alisx.git(repo, "status", "--porcelain").splitlines() if line.strip()]
    if left:
        alisx.say("%d changed file(s) are not committed, so they were not pushed." % len(left))
    kind = kind_of(repo)
    if kind == "definitions":
        alisx.say("Next: the connector's `define` works from this commit (name the branch in options if it is not the main one).")
    elif kind == "product":
        alisx.say("Next: the connector's `build` works from this commit (name the branch in options if it is not the main one).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Clone, pull and push a product's repos, signed in as the person.")
    commands = parser.add_subparsers(dest="command")
    cloning = commands.add_parser("clone", help="clone a product's code and its build environment's definitions")
    cloning.add_argument("product", help="<build environment>.<product>, for example alis.os")
    pulling = commands.add_parser("pull", help="bring the repo this is run in up to date, or both repos of a product")
    pulling.add_argument("product", nargs="?", help="<build environment>.<product>: pull its code and its definitions")
    commands.add_parser("push", help="push the branch that is checked out, in the repo this is run in")
    args = parser.parse_args()
    if args.command == "clone":
        return clone(args.product)
    if args.command == "pull":
        return pull(args.product)
    if args.command == "push":
        return push()
    parser.print_help()
    return alisx.EXIT_USAGE


if __name__ == "__main__":
    alisx.run(main)
