#!/usr/bin/env python3
"""Merge a code block's branch into the main branches, and push.

    merge_block.py <block id> <package id> [--branch <branch>]

Installing or upgrading a code block (the connector's work) leaves the block's
files on a branch, block/..., in the product's repo and in the build
environment's definitions repo. They reach the main branches only when that
branch is merged into both. This does it, and pushes both main branches.

Run it once the connector's install or upgrade has finished, and only for the
block the person asked for: it changes the main branch of two shared repos.

Each merge is prepared in a temporary checkout, so the branch, the index and
the files being worked on are not touched, and both repos are merged before
either is pushed. A block owns its service's folder alone: whatever its branch
would delete outside that folder is kept.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _alisx as alisx  # noqa: E402

git = alisx.git

PACKAGE_ID = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9-]*)+\.v[0-9]+$")
# No rename detection: from an old merge base git pairs a file the block
# deleted with a similar one in another service, and moves the block's new
# files there. A block writes its own folder and moves nothing between
# services, so such a pairing is always false. Both settings and the strategy
# option are needed: git versions differ in which they read.
NO_RENAMES = ["-c", "merge.renames=false", "-c", "merge.directoryRenames=false"]
# The Git LFS filter, for one command, so storing a file as a pointer does not
# depend on `git lfs install` having been run. required=true makes a failing
# git-lfs fail the command and not store the raw file.
LFS_FILTER = [
    "-c", "filter.lfs.clean=git-lfs clean -- %f",
    "-c", "filter.lfs.smudge=git-lfs smudge -- %f",
    "-c", "filter.lfs.process=git-lfs filter-process",
    "-c", "filter.lfs.required=true",
]
ATTEMPTS = 3


def nul(paths: list) -> str:
    return "\x00".join(paths) + "\x00"


def has_remote_branch(repo: Path, branch: str) -> bool:
    return alisx.git_ok(repo, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + branch)


def main_branch(repo: Path) -> str:
    """The repo's trunk: the remote's default branch, never a guess from names."""
    try:
        named = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").strip()
        branch = named[len("origin/"):] if named.startswith("origin/") else named
        if branch and has_remote_branch(repo, branch):
            return branch
    except alisx.GitFailed:
        pass
    try:
        for line in git(repo, "ls-remote", "--symref", "origin", "HEAD", remote=True, stdout_only=True).splitlines():
            if line.startswith("ref: refs/heads/"):
                branch = line[len("ref: refs/heads/"):].split("\t")[0]
                if branch:
                    if has_remote_branch(repo, branch):
                        alisx.git_ok(repo, "remote", "set-head", "origin", branch)
                    return branch
    except alisx.GitFailed:
        pass
    found = [candidate for candidate in ("main", "master") if has_remote_branch(repo, candidate)]
    if len(found) == 1:
        return found[0]
    raise alisx.Stop(
        "Could not tell the main branch of %s: the remote did not name its default "
        "branch, and %s. Run `git remote set-head origin <branch>` there."
        % (repo, "both origin/main and origin/master exist" if found else "neither origin/main nor origin/master exists")
    )


def owned_folder(kind: str, branch: str) -> str:
    """The folder a block's branch owns in a repo: its service's, from the branch's name."""
    package = next((s for s in branch.split("/") if s.count(".") >= 3 and PACKAGE_ID.match(s)), "")
    if not package:
        return ""
    parts = package.split(".")
    return "/".join(parts[2:]) if kind == "build" else "/".join(parts)


# --- Git LFS -----------------------------------------------------------------
# AlisX writes a block's branch without the repo's .gitattributes, so a binary
# arrives as a plain file. A merge never runs the LFS filter again, and a main
# branch that holds a plain file where .gitattributes says "LFS pointer" breaks
# every later checkout. So the block's LFS files are stored as pointers here.


def lfs_paths(worktree: Path, owned: str) -> list:
    listed = git(worktree, "ls-files", "-z", "--", owned, stdout_only=True)
    files = list(dict.fromkeys(path for path in listed.split("\x00") if path))
    if not files:
        return []
    fields = git(worktree, "check-attr", "-z", "--stdin", "filter", stdin=nul(files), stdout_only=True).split("\x00")
    return [fields[i] for i in range(0, len(fields) - 2, 3) if fields[i + 2] == "lfs"]


def require_lfs(worktree: Path, paths: list) -> None:
    if not alisx.git_ok(worktree, "lfs", "version"):
        raise alisx.Stop(
            "This repo stores %s with Git LFS, but Git LFS is not installed. Install it "
            "from https://git-lfs.com, run `git lfs install`, then merge the block again." % ", ".join(paths)
        )


def is_pointer(content: bytes) -> bool:
    return content.startswith(b"version https://git-lfs.github.com/spec/")


def blob(worktree: Path, spec: str):
    """A file's content at a commit or a merge stage, or None where there is none."""
    try:
        return alisx.git_bytes(worktree, "cat-file", "blob", spec)
    except alisx.GitFailed:
        return None


def lfs_oid(content: bytes) -> str:
    """The sha256 a pointer names, or that of content that is no pointer."""
    if is_pointer(content):
        for line in content.split(b"\n"):
            if line.startswith(b"oid sha256:"):
                return line[len(b"oid sha256:"):].strip().decode("ascii", "replace")
    return hashlib.sha256(content).hexdigest()


def only_converted(worktree: Path, path: str) -> bool:
    """Whether taking the block's version of a conflicted LFS file loses nothing of main's."""
    ours = blob(worktree, ":2:" + path)
    if ours is None:
        return False
    for stage in (":1:", ":3:"):
        other = blob(worktree, stage + path)
        if other is not None and lfs_oid(other) == lfs_oid(ours):
            return True
    return False


def commit_plain_lfs_files(worktree: Path, owned: str) -> None:
    """Before merging: store as pointers what an earlier merge committed plain."""
    paths = lfs_paths(worktree, owned)
    plain = []
    for path in paths:
        content = blob(worktree, "HEAD:" + path)
        if content is not None and not is_pointer(content):
            plain.append(path)
    if not plain:
        return
    require_lfs(worktree, plain)
    git(worktree, *LFS_FILTER, "add", "--renormalize", "--pathspec-from-file=-", "--pathspec-file-nul", stdin=nul(plain))
    git(worktree, "commit", "--no-verify", "-m", "Store %s's Git LFS files as pointers" % owned)
    git(worktree, "checkout", "--pathspec-from-file=-", "--pathspec-file-nul", stdin=nul(plain))
    alisx.say("Stored %s with Git LFS before merging: an earlier block merge committed them as plain files." % ", ".join(plain))


def store_lfs_as_pointers(worktree: Path, owned: str) -> int:
    paths = lfs_paths(worktree, owned)
    if not paths:
        return 0
    require_lfs(worktree, paths)
    conflicted = set(git(worktree, "diff", "--name-only", "-z", "--diff-filter=U", stdout_only=True).split("\x00"))
    store = []
    for path in paths:
        if path not in conflicted:
            store.append(path)
            continue
        if not only_converted(worktree, path):
            continue
        # --theirs fails when the branch deleted the file: removing it settles
        # the conflict and leaves nothing to store.
        if not alisx.git_ok(worktree, "checkout", "--theirs", "--", path):
            git(worktree, "rm", "--quiet", "--", path)
            continue
        git(worktree, *LFS_FILTER, "add", "--", path)
    if store:
        git(worktree, *LFS_FILTER, "add", "--renormalize", "--pathspec-from-file=-", "--pathspec-file-nul", stdin=nul(store))
    return len(paths)


# --- one repo's merge --------------------------------------------------------


def restore_deletions_outside(worktree: Path, owned: str) -> int:
    """Undo, in a prepared merge, every deletion the branch brings outside its folder."""
    prefix = owned.rstrip("/") + "/"
    restored = 0
    status = git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=no", stdout_only=True)
    for entry in status.split("\x00"):
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code not in ("D ", "UD") or path == owned or path.startswith(prefix):
            continue
        git(worktree, "checkout", "HEAD", "--", path)
        restored += 1
    return restored


def merge_into(worktree: Path, plan: dict) -> None:
    """Merge the block's branch into the checkout at worktree, and commit."""
    owned, remote_branch = plan["owned"], plan["remote_branch"]
    if owned:
        commit_plain_lfs_files(worktree, owned)
    refusal = None
    try:
        # The branch may start from the block's own first commit: installing a
        # block is what joins the two histories.
        git(worktree, *NO_RENAMES, "merge", "--no-commit", "--no-ff", "--allow-unrelated-histories", "-X", "no-renames", remote_branch)
    except alisx.GitFailed as failed:
        refusal = failed
    if not alisx.git_ok(worktree, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"):
        # No merge under way: with no refusal the checkout already holds the
        # branch; with one, git would not start and its words say why.
        if refusal:
            raise refusal
        return
    try:
        if owned:
            restored = restore_deletions_outside(worktree, owned)
            if restored:
                alisx.say("Kept %d file(s) outside %s/ that %s deletes: a block only changes its own folder." % (restored, owned, remote_branch))
            stored = store_lfs_as_pointers(worktree, owned)
            if stored:
                alisx.say("Stored %d file(s) in %s/ with Git LFS, as this repo's .gitattributes asks." % (stored, owned))
        unmerged = git(worktree, "diff", "--name-only", "--diff-filter=U", stdout_only=True).strip()
        if unmerged:
            raise refusal or alisx.Stop("Unresolved conflicts in %s." % unmerged.replace("\n", ", "))
        if refusal:
            alisx.say("Conflicts settled: this repo's files kept outside the block's folder, the block's Git LFS files taken inside it.")
        git(worktree, "commit", "--no-verify", "--no-edit")
    except alisx.Stop:
        alisx.git_ok(worktree, "merge", "--abort")
        raise


def plan_repo(repo: Path, kind: str, branch: str):
    remote_branch = "origin/" + branch
    git(repo, "fetch", "origin", "--prune", remote=True)
    if not alisx.git_ok(repo, "rev-parse", "--verify", "--quiet", remote_branch):
        alisx.say("No %s in %s: nothing to merge there." % (remote_branch, repo))
        return None
    main = main_branch(repo)
    # A branch that never grew past its empty first commit (the definitions of
    # a block with no protos) is on main already.
    if alisx.git_ok(repo, "merge-base", "--is-ancestor", remote_branch, "origin/" + main):
        alisx.say("%s is already part of %s in %s: nothing to merge there." % (remote_branch, main, repo))
        return None
    return {"repo": repo, "main": main, "remote_branch": remote_branch, "owned": owned_folder(kind, branch)}


def prepare(plan: dict) -> dict:
    root = Path(tempfile.mkdtemp(prefix="alisx-block-merge-"))
    worktree = root / "worktree"
    plan = dict(plan, root=root, worktree=worktree)
    try:
        git(plan["repo"], "worktree", "add", "--detach", str(worktree), "origin/" + plan["main"])
    except alisx.GitFailed:
        shutil.rmtree(str(root), ignore_errors=True)
        raise
    alisx.say("Preparing %s for %s of %s in a temporary checkout." % (plan["remote_branch"], plan["main"], plan["repo"]))
    try:
        merge_into(worktree, plan)
    except alisx.Stop as failed:
        clean_up(plan)
        raise alisx.Stop(
            "The merge failed and has to be settled by hand:\n"
            "  cd %s && git -c merge.renames=false -c merge.directoryRenames=false merge "
            "--allow-unrelated-histories -X no-renames %s\n%s" % (plan["repo"], plan["remote_branch"], failed)
        )
    return plan


def push(plan: dict) -> None:
    worktree, main, repo = plan["worktree"], plan["main"], plan["repo"]
    for attempt in range(1, ATTEMPTS + 1):
        alisx.say("Pushing the merged %s of %s." % (main, repo))
        if plan["owned"] and lfs_paths(worktree, plan["owned"]):
            # A main whose pointers name files the server lacks cannot be checked out.
            git(worktree, "lfs", "push", "origin", "HEAD", remote=True)
        try:
            git(worktree, "push", "origin", "HEAD:refs/heads/" + main, remote=True)
            alisx.say("Merged and pushed %s, without touching its checkout." % repo)
            return
        except alisx.GitFailed as failed:
            said = failed.output.lower()
            moved = "non-fast-forward" in said or "(fetch first)" in said or "failed to push some refs" in said
            if not moved:
                raise alisx.Stop("The merge was prepared, but the push failed. Nothing was pushed for this repo.\n%s" % failed)
            if attempt == ATTEMPTS:
                raise alisx.Stop("The remote %s kept changing while the block was merged. Run this again.\n%s" % (main, failed))
        alisx.say("The remote %s changed meanwhile: merging again on top of it." % main)
        git(worktree, "fetch", "origin", "--prune", remote=True)
        git(worktree, "reset", "--hard", "origin/" + main)
        merge_into(worktree, plan)


def sync_checkout(plan: dict) -> None:
    """Bring a checked-out main branch up to the merge that was pushed."""
    repo, main = plan["repo"], plan["main"]
    try:
        branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    except alisx.GitFailed:
        return
    if branch != main:
        alisx.say("The checkout of %s is on %s: merge or rebase %s into it when ready." % (repo, branch, main))
        return
    # pull.rebase=false: a personal pull.rebase=true turns --ff-only into a
    # rebase, which refuses over unrelated changed files.
    if alisx.git_ok(repo, "-c", "pull.rebase=false", "pull", "--ff-only", "origin", main, remote=True):
        alisx.say("Updated the checkout of %s." % repo)
    else:
        alisx.say(
            "The remote %s was updated, but the checkout of %s could not be moved up to it: "
            "commit or stash local changes, then `git.py pull` there." % (main, repo)
        )


def clean_up(plan: dict) -> None:
    alisx.git_ok(plan["repo"], "worktree", "remove", "--force", str(plan["worktree"]))
    shutil.rmtree(str(plan["root"]), ignore_errors=True)
    alisx.git_ok(plan["repo"], "worktree", "prune")


def merge(repos: list, branch: str) -> int:
    """Merge the branch into every repo's main branch, then push each. All or none is pushed first."""
    plans = []
    for repo, kind in repos:
        if not repo.is_dir():
            alisx.say("Skipping %s: not cloned on this machine." % repo)
            continue
        plan = plan_repo(repo, kind, branch)
        if plan:
            plans.append(plan)
    prepared = []
    try:
        try:
            for plan in plans:
                prepared.append(prepare(plan))
        except alisx.Stop as failed:
            raise alisx.Stop("No repo was changed: every repo is merged before any is pushed.\n%s" % failed)
        for plan in prepared:
            push(plan)
        for plan in prepared:
            sync_checkout(plan)
    finally:
        for plan in prepared:
            clean_up(plan)
    return len(prepared)


# --- which branch ------------------------------------------------------------


def branch_from_alisx(block: str, service) -> str:
    """The instance's branch, as AlisX records it for this block in this service."""
    answer = alisx.api("BlocksService", "ListBlockInstances", {
        "parent": "blocks/" + block,
        "filter": 'neuron = "%s"' % alisx.service_name(service),
        "pageSize": 50,
    })
    instances = [i for i in answer.get("blockInstances", []) if i.get("gitBranch") and i.get("state") != "UNINSTALLED"]
    branches = sorted({i["gitBranch"] for i in instances})
    if len(branches) > 1:
        raise alisx.Stop(
            "The block %s is installed more than once in %s. Say which with --branch: %s"
            % (block, service.package, ", ".join(branches))
        )
    return branches[0] if branches else ""


def branch_from_remotes(repos: list, block: str, package: str) -> str:
    """Failing that: the one remote block branch that names both the block and the service."""
    for repo, _ in repos:
        if not repo.is_dir():
            continue
        git(repo, "fetch", "origin", "--prune", remote=True)
        listed = git(repo, "branch", "-r", "--list", "origin/block/*", "origin/blocks/*")
        matches = []
        for line in listed.splitlines():
            name = line.strip()
            name = name[len("origin/"):] if name.startswith("origin/") else name
            segments = name.split("/")
            if name and block in segments and package in segments:
                matches.append(name)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise alisx.Stop("More than one branch could be this block's. Say which with --branch: %s" % ", ".join(matches))
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge a code block's branch into the main branches, and push.")
    parser.add_argument("block", help="the block's id, for example simpleapi")
    parser.add_argument("package", help="the service the block was installed in, by package id")
    parser.add_argument("--branch", default="", help="the block's branch, when it cannot be told (block/...)")
    args = parser.parse_args()

    service = alisx.Service(args.package)
    repos = [
        (alisx.build_root(service.organisation, service.product), "build"),
        (alisx.define_root(service.organisation), "define"),
    ]
    if not any(repo.is_dir() for repo, _ in repos):
        raise alisx.Stop(
            "Neither repo is cloned on this machine. Run: git.py clone %s.%s" % (service.organisation, service.product)
        )

    branch = args.branch.strip()
    if branch.startswith("origin/"):
        branch = branch[len("origin/"):]
    if not branch:
        branch = branch_from_alisx(args.block, service) or branch_from_remotes(repos, args.block, service.package)
    if not branch:
        raise alisx.Stop(
            "AlisX names no branch for the block %s in %s, and no block branch on the remotes "
            "matches. Has the connector's install or upgrade finished? Wait for its operation, "
            "then run this again." % (args.block, service.package)
        )

    alisx.say("Merging %s." % branch)
    merged = merge(repos, branch)
    if merged == 0:
        alisx.say("Nothing was left to merge: the block's files are on the main branches already.")
    else:
        alisx.say("Done: the block's files are on the main branch of %d repo(s)." % merged)
    alisx.say("Next: take its packages in if it brought protos (the connector's `define`, then install_packages.py), then `build`.")
    return 0


if __name__ == "__main__":
    alisx.run(main)
