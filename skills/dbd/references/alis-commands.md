# When a skill or an answer names an `alis` command

Many skills in the registry were written for the AlisX CLI (`alis ...`) or for an older connector. Where the CLI is not installed, the same work is done by a connector tool or by one of this skill's scripts. Follow the skill's steps and substitute from the tables.

When unsure what a connector call takes, ask its `describe` tool: `describe` with a tool and a kind (`create service`) lists every option.

## `alis` commands

| The skill says | Do this |
| --- | --- |
| `alis clone <build environment>.<product>` | `git.py clone <build environment>.<product>` |
| `git clone`, `git pull`, `git push` on a product's or the definitions repo | `git.py clone`, `git.py pull`, `git.py push` |
| `alis define <package> --dry-run` | Connector: `read`, thing `services/<package>`, part `definition check` |
| `alis define <package>` | Connector: `define`, then `wait` on its operation |
| `alis define <package> --install` | The same, then `install_packages.py <package> --version <the new version>` |
| `alis packages install <package>` | `install_packages.py <package>` |
| `alis packages upgrade <package>` | `install_packages.py <package>`, or with `--upgrade-all` for third-party packages too |
| `alis packages add <package>` | `install_packages.py <package> --add` |
| `alis build <package>` | Connector: `build`, then `wait`. Options may name deployment environments to deploy to once built |
| `alis deploy <package>` | Connector: `deploy`, with `version` and `environments`, then `wait` |
| `alis operations wait <operation>` | Connector: `wait`, thing `operations/<id>` |
| `alis logs`, build or deploy logs | Connector: `read`, thing `services/<package>`, part `logs`; or thing `operations/<id>`, part `build logs` or `deploy logs` |
| `alis environment list` | Connector: `read`, thing `products/<build environment>.<product>`, part `environments` |
| `alis environment refresh` | `fetch_envs.py <build environment>.<product> <environment>` |
| `alis environment set`, `unset` | Connector: `update` or `delete`, thing `products/<...>/environments/<id>`, part `variables` |
| `alis service new <package>` | Connector: `create`, kind `service`. Then make its folders by hand (see SKILL.md, "Where things are") |
| `alis blocks install`, `upgrade`, `uninstall` | Connector: `create`, `update`, `delete` (see `blocks.md`) |
| `alis blocks merge` | `merge_block.py <block id> <package>` |
| `alis blocks create` | `create_block.py` (see `blocks.md`) |
| `alis blocks publish` | Connector: `publish`, thing `blocks/<block id>`, with the service in `options.package` |
| `alis skills search`, `suggest`, `load` | Connector: `search`, kind `skill`; `read`, thing `skills/<id>` |
| `alis skills create`, `edit`, `publish`, `share`, `feedback` | Connector: `create`, `update`, `share`, `send` on the skill |
| `alis artifacts create` | Connector: `create`, kind `artifact`; a large or binary file through kind `upload` first |
| `alis ideate context <project>` | Connector: `read`, thing `ideas/<id>` |
| `alis specialist ...` | Connector: `search`, `read`, `send` on conversations (`tickets/<id>`) |
| `alis catch-up` | Connector: `read`, thing `catch-up` |
| `alis whoami` | Connector: `read`, thing `me` |

`--json`, `--async` and `--approve` have no counterpart: a connector tool answers in words, starts an operation to `wait` on, and asks the person itself where that is needed. `--confirm-production` is the connector's `confirm_production`, passed only on the person's word.

## No counterpart here

These need the AlisX CLI on the machine. Say so; do not improvise them.

- `alis run`, `alis preview`: running a service locally and opening its page.
- `alis workstation handoff`: moving a session to a workstation.
- `alis doctor`, `alis upgrade`, `alis login`: the CLI's own upkeep.
- `.NET`, Swift, Rust, Java and Kotlin packages: `install_packages.py` sets up Go, Node, Python and Dart.

## Older tool names

| The skill says | Do this |
| --- | --- |
| `define_service`, `build_service`, `deploy_service` | The connector's `define`, `build`, `deploy` |
| `wait_for_operation`, `get_operation` | `wait` or `read`, thing `operations/<id>` |
| `load_skill`, `search_skills`, `list_skills`, MCP `LoadSkill` | `read`, thing `skills/<id>`; `search`, kind `skill` |
| `view_product`, MCP `ViewProduct` | `read`, thing `products/<build environment>.<product>` |
| `view_build_environment`, MCP `GetLandingZone` | `read`, thing `build-environments/<id>` |
| `get_git_credentials` | Nothing to fetch: `git.py` signs git in |
| `reveal_environment_variables` | `fetch_envs.py`, which writes the values to a file and never shows them |
| `run_sandbox_command` and the other sandbox tools | Run the command on this machine |

Any other older name is a connector tool named for what it does (`create_artifact` is `create`, kind `artifact`). `describe` with the kind lists the calls there are.

An organisation is a build environment, a landing zone is a build environment, and a neuron is a service: older skills use the first word of each pair.
