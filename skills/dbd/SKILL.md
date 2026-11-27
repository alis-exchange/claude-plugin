---
name: dbd
description: Work on an Alis Build service's code on this machine, from clone to deploy. Carries the scripts for what the AlisX connector cannot do itself - clone, pull and push a product's repos (git.py), take a defined version's packages into the code (install_packages.py), write a deployment environment's variables to a local env file (fetch_envs.py), run a service on this machine against an environment (run.py), create or merge a code block (create_block.py, merge_block.py), and connect this machine (connect.py). Use when changing a service's protos or code, when running Define, Build, Deploy, when the connector's answer names one of these scripts or a step for the person's machine, when a skill names an `alis` command and the AlisX CLI is not installed, or when a script says the machine is not connected. Plain git clone, pull and push fail on these repos, and registry files or version pins written by hand go stale.
---

# Define, Build, Deploy from this machine

The AlisX connector does the platform's work: define, build, deploy, environments, logs. The scripts here do what has to happen where the code is. A change to a service goes back and forth between the two, and this file is the order.

## The scripts

They are in `scripts/`, beside this file. Run one as `python3 <this skill's folder>/scripts/<name>.py`. They need only Python 3 and git: nothing to install. Each prints what it did and what comes next, fails with one line that says what to do, and explains itself with `--help`. Read `--help` for options; they are not repeated here.

| Script | What it does |
| --- | --- |
| `git.py clone <build environment>.<product>` | Clones the product's code and its build environment's definitions |
| `git.py pull [--rebase]`, `git.py push` | Pulls or pushes the branch checked out in the repo it is run in; `--rebase` puts this checkout's commits on top of the remote's, and undoes a rebase that stops |
| `install_packages.py <package id> [--version <version>]` | Takes a defined version's packages into the service's code; `--auth-only` only signs in to the package registries |
| `fetch_envs.py <build environment>.<product> <environment>` | Writes an environment's variables to `.alis/.env` in the product's clone |
| `run.py <package id> <environment> [-- <command>]` | Runs the service here against an environment: its variables, its key and the package registries, then `go run .`, `npm start` or the command given |
| `create_block.py`, `merge_block.py` | Code blocks: see `references/blocks.md` |
| `connect.py <code> ...` | Connects this machine, with the code the connector hands out |

## Where things are

A service is named by its package id. `alis.os.users.v1` is the build environment `alis`, the product `os`, the service `users`, version `v1`.

```
~/alis.build/<build environment>/define/<build environment>/<product>/<service>/v<N>   its protos
~/alis.build/<build environment>/build/<product>/<service>/v<N>                         its code, with Terraform in infra/
```

`ALIS_WORKSPACE_ROOT`, when set, stands in for `~/alis.build`. The definitions are one repo for the whole build environment; each product's code is a repo of its own.

Creating a service with the connector makes its record and no folders. Make the two folders above, and `infra/`, by hand in the clones.

## The loop

Each step says who does it.

1. **Script.** `git.py clone <build environment>.<product>` once. Later, `git.py pull <build environment>.<product>` before starting.
2. **You.** Edit the protos. Check them before pushing with the connector: `read`, thing `services/<package id>`, part `definition check`.
3. **Script.** Commit, then `git.py push` in the definitions repo.
4. **Connector.** `define` the service, then `wait` on the operation it names. The answer gives the new version.
5. **Script.** `install_packages.py <package id> --version <that version>`.
6. **You.** Write the code, build it and test it. To run it against an environment: `run.py <package id> <environment>`.
7. **Script.** Commit, then `git.py push` in the product's repo.
8. **Connector.** `build` the service, `wait`, then `deploy` it. A production environment only on the person's own word.

A change that touches no proto starts at step 6. Define, build and deploy read what was pushed, never what is only on this machine: an unpushed commit does not exist for them.

## When a script says the machine is not connected

It exits with code 3 and a line that starts `Not connected`. Then:

1. Call the connector's `connect` tool. It hands out a sign-in as the person, so call it only because a script said so, for work the person asked for.
2. Run the command its answer prints, whole and at once: `connect.py <code> --client-id ... --identity ...`. The code works once, for ten minutes.
3. Run the script that stopped again.

Never repeat the code in an answer, and never write it to a file. One connect lasts: do not call `connect` again unless a script says so.

`Could not reach <host>` is a different failure, and `connect` does not fix it. The machine has no network path to that host. In a cloud session, the environment's allowed domains have to include it; tell the person which host.

## Rules

- **Git.** Clone, pull and push go through `git.py`. Everything that stays on the machine is plain git: status, add, commit, branch, diff, log, merge, rebase. Never force a push, and never put a token in a remote's address or in git's settings.
- **Packages.** Never write `.netrc`, `.npmrc`, a `replace` in `go.mod` or a version by hand to make packages resolve. `install_packages.py` does both the sign-in and the version, and a hand-made one stops working within the hour.
- **Secrets.** Never print, `cat` or read `.alis/.env` or `.alis/key.json` into the conversation, and never paste a value from them. Load them into the command that needs them with the line `fetch_envs.py` ends on. Changing a variable is the connector's `update`, not this script.
- **Production.** `fetch_envs.py --production`, `run.py --production` and the connector's `confirm_production` are passed only when the person said so in this conversation.
- **Building.** The connector's `build` takes, in `build_paths`, only the Dockerfiles whose code changed: the service's other images are retagged from its last build.
- **Code leaving the machine.** `create_block.py` sends a service's files to AlisX. Show the person what it lists and pass `--confirm` only on their yes.

## More

- Installing, upgrading, creating or publishing a code block: `references/blocks.md`.
- A skill or an answer names an `alis` command, or an older tool name: `references/alis-commands.md` says what does that work here.
