# Code blocks

A code block is a service's code, Terraform and protos, packaged for other services to install. The connector does the platform's half. Two scripts do the machine's half: `merge_block.py` and `create_block.py`.

## Install or upgrade a block in a service

1. **Connector.** Install: `create`, kind `installed code block`, in `services/<package id>`. Upgrade: `update`, thing `services/<package id>/blocks/<block id>`. Either starts an operation: `wait` on it.
2. **Script.** `merge_block.py <block id> <package id>`.
3. If the block brought protos: the connector's `define`, `wait`, then `install_packages.py <package id>`.
4. **Connector.** `build` and `deploy` as usual.

Step 1 leaves the block's files on a branch (`block/...`) in the product's repo and in the definitions repo. Nothing is installed until step 2 merges that branch into both main branches and pushes them.

`merge_block.py` changes the main branch of two shared repos. Run it only for the block the person asked for, after the operation has finished.

What it guarantees:

- The checkout being worked in is not touched while it merges: each merge is made in a temporary checkout. Afterwards a checkout that is on the main branch is moved up to the merge; one on another branch is left alone and named.
- Both repos are merged before either is pushed, so a conflict in one never leaves only the other merged.
- A block changes its own service's folder and nothing else. Whatever its branch would delete outside that folder is kept.
- Files the repo keeps in Git LFS are stored as LFS pointers, as `.gitattributes` asks.

When it fails with a conflict, nothing was pushed. It prints the git command to settle the merge by hand in the repo. Settle it, commit, and push with `git.py push`.

When it finds no branch, the operation has usually not finished: `wait` on it and run the script again. If a block is installed more than once in a service, it lists the branches; pass the right one with `--branch`.

To remove a block from a service: the connector's `delete`, thing `services/<package id>/blocks/<block id>`.

## Create a block from a service

1. **Connector.** Find the account that will own it: `search`, kind `code block account`.
2. **Script.** `create_block.py <block id> <package id> --account accounts/<id> --name "<display name>"`. This sends nothing. It lists what would go: the service's code, its `infra/` and its protos, with the files it leaves out.
3. Show the person the list. On their yes, run the same command with `--confirm`.
4. Fill in `.skill/SKILL.md` in the service's folder, which the script created. It becomes the block's skill.
5. **Script.** Commit, then `git.py push`.
6. **Connector.** Publish the block's first version: `publish`, thing `blocks/<block id>`, with the service's package id in `options.package`. It reads pushed commits only, and starts an operation to `wait` on. `describe` with `publish code block` lists the other options (release level, notes).

Always left out of a block: `.git`, `node_modules`, editor and agent folders, `.alis`, every `.env*` file and `key.json`. A single file over 512 KiB is skipped and named. More than 3 MiB in all is refused: remove build output and fixtures, or start from a leaner service.

A new version of an existing block is steps 5 and 6 alone.
