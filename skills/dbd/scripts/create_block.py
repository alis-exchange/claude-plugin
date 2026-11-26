#!/usr/bin/env python3
"""Create a code block from a service's code.

    create_block.py <block id> <package id> --account accounts/<id> --name "<display name>"
    create_block.py ... --confirm

A code block is a service's code, its Terraform and its protos, packaged for
other services to install. This collects those files from the service's
folders on this machine and sends them to AlisX.

Without --confirm it sends nothing: it lists what would go, for the person to
see. Run it again with --confirm only on their yes, since the files leave the
machine.

Left out always: .git, node_modules, editor and agent folders, .alis, .env*
files and key.json. A file over 512 KiB is skipped and named; more than 3 MiB
in all is refused.

The account comes from the connector (search, kind "code block account").
Publishing a version of the block afterwards is the connector's `publish`.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _alisx as alisx  # noqa: E402

PER_FILE_CAP = 512 << 10
TOTAL_CAP = 3 << 20
EXCLUDED = {
    "node_modules", ".gemini", ".codex", ".claude", ".dart_tool", ".idea", ".cursor", ".vscode",
    ".git", ".DS_Store", "CODEBLOCK_LICENSE.md", ".alis", ".playground", "key.json",
}
BLOCK_ID = re.compile(r"^[a-z][a-z0-9-]*$")

SKILL_TEMPLATE = """%(front)s# %(name)s

Step-by-step instructions an agent should follow to work with this block.

<!--
This .skill/ folder becomes the block's skill in the AlisX skill registry once
any version of the block is published (the most stable available release
supplies the skill's content).

The skill's id (front-matter name) is set automatically to block-%(block)s;
any name added here is overridden. If description is omitted, the block's
overview is used.

Optional structure, per the Agent Skills format (https://agentskills.io/):
  references/  supplementary documents loaded on demand
  scripts/     runnable helpers
  assets/      images and other static files
-->
"""


def excluded(name: str) -> bool:
    return name in EXCLUDED or name.startswith(".env")


def size(count: int) -> str:
    if count >= 1 << 20:
        return "%.1f MiB" % (count / float(1 << 20))
    if count >= 1 << 10:
        return "%.0f KiB" % (count / float(1 << 10))
    return "%d B" % count


class Collection:
    def __init__(self):
        self.build, self.infra, self.proto, self.skipped = [], [], [], []
        self.total = 0

    def walk(self, root: Path, bucket: str) -> None:
        for directory, subfolders, files in os.walk(str(root)):
            subfolders[:] = sorted(name for name in subfolders if not excluded(name) and not (Path(directory) / name).is_symlink())
            for name in sorted(files):
                path = Path(directory) / name
                # A link may point outside the service: blocks carry real files only.
                if excluded(name) or path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                length = path.stat().st_size
                if length > PER_FILE_CAP:
                    self.skipped.append((bucket + "/" + relative, length))
                    continue
                entry = {"filename": relative, "content": path.read_bytes()}
                if bucket == "proto":
                    self.proto.append(entry)
                elif relative == "infra" or relative.startswith("infra/"):
                    entry["filename"] = relative[len("infra/"):]
                    self.infra.append(entry)
                else:
                    self.build.append(entry)
                self.total += len(entry["content"])

    def largest(self, count: int) -> str:
        every = [("build/" + f["filename"], len(f["content"])) for f in self.build]
        every += [("infra/" + f["filename"], len(f["content"])) for f in self.infra]
        every += [("proto/" + f["filename"], len(f["content"])) for f in self.proto]
        every.sort(key=lambda item: -item[1])
        return ", ".join("%s (%s)" % (name, size(length)) for name, length in every[:count])


def scaffold_skill(folder: Path, block: str, name: str, tagline: str) -> bool:
    """Write a starter .skill/SKILL.md where the service has none."""
    target = folder / ".skill" / "SKILL.md"
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    front = "---\ndescription: %s\n---\n\n" % tagline.strip() if tagline.strip() else ""
    target.write_text(SKILL_TEMPLATE % {"front": front, "name": name.strip() or block, "block": block}, encoding="utf-8")
    return True


def encoded(files: list) -> list:
    return [{"filename": f["filename"], "content": base64.b64encode(f["content"]).decode("ascii")} for f in files]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a code block from a service's code.")
    parser.add_argument("block", help="the new block's id: lowercase letters, digits and hyphens, for example sendgrid")
    parser.add_argument("package", help="the service whose code seeds the block, by package id, for example alis.os.mail.v1")
    parser.add_argument("--account", required=True, help="the account that will own the block: accounts/<id>, from the connector")
    parser.add_argument("--name", required=True, help='the block\'s display name, for example "SendGrid Email Dispatcher"')
    parser.add_argument("--tagline", default="", help="a short line on what the block does")
    parser.add_argument("--confirm", action="store_true", help="send the files. Pass it only after the person saw what goes and said yes")
    args = parser.parse_args()

    if not BLOCK_ID.match(args.block):
        raise alisx.Stop("%r is not a block id: lowercase letters, digits and hyphens only, starting with a letter." % args.block, alisx.EXIT_USAGE)
    if not args.account.startswith("accounts/"):
        raise alisx.Stop("%r is not an account. Name it accounts/<id>: the connector lists them (search, kind \"code block account\")." % args.account, alisx.EXIT_USAGE)
    if not args.name.strip():
        raise alisx.Stop("Give the block a display name with --name.", alisx.EXIT_USAGE)

    service = alisx.Service(args.package)
    folder = service.build_folder
    if not folder.is_dir():
        raise alisx.Stop(
            "The service's folder %s is not on this machine. Clone the product first: "
            "git.py clone %s.%s" % (folder, service.organisation, service.product)
        )

    made = scaffold_skill(folder, args.block, args.name, args.tagline)
    files = Collection()
    files.walk(folder, "build")
    if service.define_folder.is_dir():
        files.walk(service.define_folder, "proto")
    if not (files.build or files.infra or files.proto):
        raise alisx.Stop("No files were found to contribute in %s." % folder)
    if files.total > TOTAL_CAP:
        raise alisx.Stop(
            "The service's files total %s, above the %s limit for a block. Largest: %s. "
            "Remove artifacts or use a leaner service." % (size(files.total), size(TOTAL_CAP), files.largest(3))
        )

    if made:
        alisx.say("Created %s: it becomes the block's skill on its first published version. Fill it in." % (folder / ".skill" / "SKILL.md"))
    alisx.say("Block %s (%s), owned by %s, from %s:" % (args.block, args.name, args.account, service.package))
    alisx.say("  code:      %d file(s) from %s" % (len(files.build), folder))
    alisx.say("  Terraform: %d file(s) from %s" % (len(files.infra), folder / "infra"))
    alisx.say("  protos:    %d file(s) from %s" % (len(files.proto), service.define_folder))
    alisx.say("  in all:    %s" % size(files.total))
    for name, length in files.skipped:
        alisx.say("  skipped, over %s: %s (%s)" % (size(PER_FILE_CAP), name, size(length)))

    if not args.confirm:
        alisx.say()
        alisx.say("Nothing was sent. These files leave the machine when the block is created: show")
        alisx.say("the person this, and run the same command with --confirm on their yes.")
        return 0

    answer = alisx.api("BlocksService", "CreateBlock", {
        "neuron": alisx.service_name(service),
        "blockId": args.block,
        "displayName": args.name.strip(),
        "tagline": args.tagline.strip(),
        "account": args.account,
        "buildFiles": encoded(files.build),
        "infraFiles": encoded(files.infra),
        "protoFiles": encoded(files.proto),
    })
    alisx.say()
    alisx.say("Created the block %s." % (answer.get("block") or "blocks/" + args.block))
    if answer.get("instance"):
        alisx.say("Its source instance, to publish versions from: %s" % answer["instance"])
    alisx.say("Next: commit and push the service (git.py push), then publish the block's first")
    alisx.say("version with the connector: `publish`, thing blocks/%s, options.package %s." % (args.block, service.package))
    alisx.say("A version is made from pushed commits.")
    return 0


if __name__ == "__main__":
    alisx.run(main)
