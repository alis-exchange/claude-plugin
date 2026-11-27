#!/usr/bin/env python3
"""Clone, pull and push a product's repos, signed in as the person.

    git.py clone <build environment>.<product>
    git.py pull [<build environment>.<product>] [--rebase]
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


# git with Git LFS's filters off: files are compared and written as they are
# stored, not as the repo's LFS rules would store them.
LFS_OFF = ("-c", "filter.lfs.process=", "-c", "filter.lfs.clean=", "-c", "filter.lfs.smudge=",
           "-c", "filter.lfs.required=false")


def stored_before_lfs(repo: Path) -> list[str]:
    """Files git shows as changed only because the repo's LFS rules came after them.

    A file that matches the rules in .gitattributes but was committed as a plain
    file reads as changed wherever git-lfs is installed, though nobody changed
    it: with LFS's filters off it is the same. Such files are not the person's
    changes, and they are what made a rebase refuse ("You have unstaged changes").
    """
    shown = set(alisx.git(repo, "diff", "--name-only", stdout_only=True).split("\n"))
    real = set(alisx.git(repo, *LFS_OFF, "diff", "--name-only", stdout_only=True).split("\n"))
    return sorted(f for f in shown - real if f)


def say_stored_before_lfs(repo: Path, files: list[str]) -> None:
    if files:
        alisx.say(
            "%d file(s) in %s show as changed but are not: they match the repo's LFS rules and were "
            "committed before them (%s%s). Leave them out of commits: add only your own files "
            "(git add <path>), never git add -A or git commit -a."
            % (len(files), repo, ", ".join(files[:3]), ", ..." if len(files) > 3 else "")
        )


def after_lfs_off(repo: Path) -> None:
    """Files a pull wrote with LFS's filters off get their large content back."""
    if alisx.git_ok(repo, "lfs", "version"):
        alisx.git_ok(repo, "lfs", "checkout", remote=True)


def rebasing(repo: Path) -> bool:
    """Whether a rebase stopped part way in the repo."""
    git_dir = Path(alisx.git(repo, "rev-parse", "--git-dir", stdout_only=True).strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def pull_repo(repo: Path, rebase: bool = False) -> str:
    branch = branch_of(repo)
    if rebase:
        return rebase_repo(repo, branch)
    phantoms = stored_before_lfs(repo)
    off = LFS_OFF if phantoms else ()
    try:
        alisx.git(repo, *off, "-c", "pull.rebase=false", "pull", "--ff-only", "origin", branch, remote=True)
        if off:
            after_lfs_off(repo)
    except alisx.GitFailed as failed:
        said = failed.output.lower()
        if "couldn't find remote ref" in said or "could not find remote ref" in said:
            return "%s: %s is not on the remote yet, so there is nothing to pull." % (repo, branch)
        if "not possible to fast-forward" in said or "diverg" in said or "overwritten" in said:
            raise alisx.Stop(
                "%s could not be brought up to date: %s and its remote have each moved, "
                "or local changes are in the way. Nothing was changed. To put this "
                "checkout's own commits on top of the remote's, run `git.py pull --rebase`.\n%s"
                % (repo, branch, failed.output.strip())
            )
        raise
    say_stored_before_lfs(repo, phantoms)
    return "%s: %s is up to date with its remote, at %s." % (repo, branch, head(repo))


def rebase_repo(repo: Path, branch: str) -> str:
    """Put this checkout's commits on top of the remote's, or change nothing."""
    before = alisx.git(repo, "rev-parse", "HEAD", stdout_only=True).strip()
    # Files that only look changed (stored_before_lfs) are compared as they
    # are stored, so that they neither stop the rebase nor are set aside for it.
    phantoms = stored_before_lfs(repo)
    off = LFS_OFF if phantoms else ()
    try:
        alisx.git(repo, *off, "-c", "rebase.autoStash=true", "pull", "--rebase", "origin", branch, remote=True)
        if off:
            after_lfs_off(repo)
    except alisx.GitFailed as failed:
        said = failed.output.lower()
        if "couldn't find remote ref" in said or "could not find remote ref" in said:
            return "%s: %s is not on the remote yet, so there is nothing to pull." % (repo, branch)
        # Nothing is left half done: a rebase that stopped is undone, which
        # also puts back any changes that were set aside for it.
        conflicts = ""
        if rebasing(repo):
            conflicts = alisx.git(repo, "diff", "--name-only", "--diff-filter=U", stdout_only=True).strip()
            alisx.git_ok(repo, *off, "rebase", "--abort")
        where = (" Both changed: %s." % ", ".join(conflicts.split())) if conflicts else ""
        raise alisx.Stop(
            "%s could not be rebased onto origin/%s, so it was left as it was, at %s.%s "
            "Resolve it with plain git (git rebase origin/%s, fix each file, git rebase --continue), "
            "then git.py push.\n%s" % (repo, branch, before[:12], where, branch, failed.output.strip())
        )
    say_stored_before_lfs(repo, phantoms)
    return "%s: %s is on top of its remote, at %s." % (repo, branch, head(repo))


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
    say_stored_before_lfs(folder, stored_before_lfs(folder))


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


def pull(product: str | None, rebase: bool = False) -> int:
    if product:
        organisation, product_id = alisx.split_product(product)
        repos = [alisx.define_root(organisation), alisx.build_root(organisation, product_id)]
        missing = [str(repo) for repo in repos if not is_repo(repo)]
        if missing:
            raise alisx.Stop("Not cloned yet: %s. Run: git.py clone %s" % (", ".join(missing), product))
    else:
        repos = [top(Path.cwd())]
    for repo in repos:
        alisx.say(pull_repo(repo, rebase))
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
                "and nothing was forced. Run `git.py pull --rebase` to put this checkout's "
                "commits on top of them, then push again.\n%s" % (branch, failed.output.strip())
            )
        raise
    commit = alisx.git(repo, "rev-parse", "HEAD").strip()
    alisx.say("Pushed %s at %s to its remote." % (branch, head(repo)))
    alisx.say("Commit: %s" % commit)
    phantoms = set(stored_before_lfs(repo))
    left = [line for line in alisx.git(repo, "status", "--porcelain").splitlines()
            if line.strip() and line[3:].strip() not in phantoms]
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
    pulling.add_argument("--rebase", action="store_true", help="put this checkout's own commits on top of the remote's, when both have moved; a rebase that stops is undone")
    commands.add_parser("push", help="push the branch that is checked out, in the repo this is run in")
    args = parser.parse_args()
    if args.command == "clone":
        return clone(args.product)
    if args.command == "pull":
        return pull(args.product, args.rebase)
    if args.command == "push":
        return push()
    parser.print_help()
    return alisx.EXIT_USAGE


if __name__ == "__main__":
    alisx.run(main)
