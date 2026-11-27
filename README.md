# claude-plugin

Plugin for Claude Code and Claude Web with an MCP connector, skills and hooks that give the best possible experience with the AlisX Platform in Claude.

## Install

In Claude Code:

```
/plugin marketplace add alis-exchange/claude-plugin
/plugin install alis@alisx
```

The plugin is named `alis`, as the earlier AlisX plugin for the CLI is. Turn that one off first (`/plugin disable alis@alis`): the two give opposite advice on whether to use the CLI.

If your Claude account already has the AlisX connector (added in Claude's settings), Claude Code may list the plugin's own entry beside it as needing a sign-in, and tell you it cannot reach AlisX until you sign it in. Sign it in once with `/mcp`: both are the same server.

## What is in it

| Part | Where | What it is for |
| --- | --- | --- |
| The AlisX connector | `.mcp.json` | The platform's work: skills, Alis Ideate, artifacts, and Define, Build, Deploy. It is the server `alis.os.mcp.v2` at `https://mcp-v2.alisx.com` |
| The `dbd` skill | `skills/dbd/` | The machine's work, and the order in which machine steps and connector calls alternate |
| Seven scripts | `skills/dbd/scripts/` | What has to happen where the code is |

There is one skill on purpose. The connector describes itself (its instructions, its tools, its `describe` tool), and how to do a piece of work lives in the skill registry, which the connector searches. The plugin's skill holds only what neither can know: the machine's half.

There are no hooks yet. Everything here works where no hook runs.

## The scripts

Python 3 and git, standard library only: nothing to install.

| Script | What it does |
| --- | --- |
| `connect.py` | Trades the connector's one-time code for a sign-in kept on the machine |
| `git.py` | `clone`, `pull` (`--rebase` too) and `push` a product's repos, signed in for that one command |
| `install_packages.py` | Signs the machine in to a product's package registries and runs the install commands AlisX generates (`--auth-only`: the sign-in alone) |
| `fetch_envs.py` | Writes a deployment environment's variables to `.alis/.env` in the product's clone |
| `run.py` | Runs a service on the machine against a deployment environment: its variables, its key and the package registries |
| `create_block.py` | Creates a code block from a service's files |
| `merge_block.py` | Merges an installed block's branch into the main branches and pushes |

`_alisx.py` is what they share: the sign-in, the calls to the API, names and folders, and git.

## How sign-in works

1. Claude signs in to the connector by identifying itself (a Client ID Metadata Document). The plugin holds the connector's address and nothing else: no client id, no secret.
2. A script that needs the platform says the machine is not connected. Claude calls the connector's `connect` tool, and the answer is a one-time code in the command to run.
3. `connect.py` trades the code at the identity service for a sign-in, kept in `~/.alisx/plugin/` where only the person can read it. No client secret is involved.
4. Each script renews a short-lived token from that sign-in. The token is for the AlisX API (`api.alisx.com`) and for build work alone (`build:read build:write`). It is also the password git is given, for the one command that needs it.

The scripts call the public API in JSON over HTTPS: `POST https://api.alisx.com/alis.os.gateway.v1.<Service>/<Method>`.

| Setting | Default | What it changes |
| --- | --- | --- |
| `ALISX_PLUGIN_HOME` | `~/.alisx/plugin` | Where the sign-in is kept |
| `ALISX_API_URL` | `https://api.alisx.com`, or `https://api.alisx.dev` for a Development sign-in | The API's address |
| `ALISX_GIT_URL` | `https://git.{organisation}.alis.dev/{organisation}` | A build environment's git host |
| `ALIS_WORKSPACE_ROOT` | `~/alis.build` | Where products are cloned, as for the AlisX CLI |

## What the scripts depend on

They need these on the platform's side:

- `alis.os.gateway.v1`: `NeuronsService.GeneratePackageScripts` and `BlocksService.CreateBlock`, beside the methods it already has.
- `alis.os.mcp.v2`: `connect` making its code for the app "Claude", for the API.
- `alis.os.iam.v2`: the connector's address among those a client that identifies itself may sign in to.
- The git host accepting an API token that carries `build:write`.

## Testing

```sh
python3 -m unittest discover -s tests
```

The tests run each script as its own process against a stand-in for the identity service and the API (`tests/fake.py`), a temporary home folder and throwaway git repos. Nothing reaches the network. They need git, and git-lfs for one case that is skipped without it.

## Not here yet

- **Five languages.** `install_packages.py` sets up Go, Node, Python and Dart. .NET, Swift, Rust, Java and Kotlin are named and left to the AlisX CLI.
- **Hooks.** None are written: service context at session start, a secret warning and handoff are the candidates.
- **Running a service locally, previews and workstation handoff** stay with the AlisX CLI.
