"""The plugin's scripts, run as they are run for real.

    python3 -m unittest discover -s tests

Each test runs a script as its own process against a stand-in for AlisX
(tests/fake.py) and a temporary home folder. Nothing reaches the network.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake import SCRIPTS, Machine, Platform, Refused  # noqa: E402

SERVICE = "organisations/alis/products/os/neurons/users-v1"


class ScriptTest(unittest.TestCase):
    def setUp(self):
        self.platform = Platform()
        self.machine = Machine(self.platform)
        self.addCleanup(self.platform.close)
        self.addCleanup(self.machine.close)
        self.platform.methods["NeuronsService/ListNeurons"] = lambda body: {
            "neurons": [{"name": SERVICE, "packageId": "alis.os.users.v1"}]
        }

    def private(self, path: Path) -> None:
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, "%s must be readable by its owner alone" % path)


class Connecting(ScriptTest):
    def test_a_script_says_it_is_not_connected(self):
        for script, args in (("connect.py", ["--status"]), ("fetch_envs.py", ["alis.os", "Development"]), ("git.py", ["clone", "alis.os"])):
            code, out, err = self.machine.run(script, *args)
            self.assertEqual(code, 3, script)
            self.assertIn("Not connected", err)
            self.assertIn("`connect` tool", err)

    def test_the_code_becomes_a_sign_in_kept_private(self):
        code, out, err = self.machine.run("connect.py", "good-code", "--client-id", "the-claude-app", "--identity", self.platform.address)
        self.assertEqual(code, 0, err)
        self.assertIn("Connected as builder@example.com", out)
        asked = self.platform.token_requests[0]
        self.assertEqual(asked["grant_type"], "authorization_code")
        self.assertEqual(asked["redirect_uri"], "offline")
        self.assertEqual(asked["client_id"], "the-claude-app")
        self.assertEqual(asked["resource"], "https://api.alisx.com")
        self.assertNotIn("client_secret", asked)
        kept = self.machine.home / ".alisx" / "plugin" / "sign-in.json"
        self.private(kept)
        self.assertEqual(json.loads(kept.read_text())["refresh_token"], "refresh-1")
        # Each token the scripts ask for is for the API and for build work alone.
        renewed = [r for r in self.platform.token_requests if r["grant_type"] == "refresh_token"][0]
        self.assertEqual((renewed["resource"], renewed["scope"], renewed["client_id"]), ("https://api.alisx.com", "build:read build:write", "the-claude-app"))
        self.assertNotIn("good-code", out + err)

    def test_a_used_code_is_refused_in_words(self):
        code, out, err = self.machine.run("connect.py", "old-code", "--client-id", "the-claude-app", "--identity", self.platform.address)
        self.assertEqual(code, 1)
        self.assertIn("good once and for ten minutes", err)
        self.assertFalse((self.machine.home / ".alisx" / "plugin" / "sign-in.json").exists())

    def test_a_sign_in_that_ended_says_to_connect_again(self):
        self.machine.connect()
        self.platform.refresh_tokens.clear()
        (self.machine.home / ".alisx" / "plugin" / "token.json").unlink()
        code, out, err = self.machine.run("connect.py", "--status")
        self.assertEqual(code, 3)
        self.assertIn("no longer good", err)

    def test_disconnect_removes_the_sign_in(self):
        self.machine.connect()
        code, out, err = self.machine.run("connect.py", "--disconnect")
        self.assertEqual(code, 0)
        self.assertFalse((self.machine.home / ".alisx" / "plugin" / "sign-in.json").exists())


class FetchingEnvs(ScriptTest):
    def setUp(self):
        super().setUp()
        self.machine.connect()
        self.repo = self.machine.workspace / "alis" / "build" / "os"
        self.repo.mkdir(parents=True)
        self.machine.git(self.repo, "init", "--quiet")
        product = "organisations/alis/products/os"
        self.platform.methods["EnvironmentsService/ListEnvironments"] = lambda body: {"environments": [
            {"name": product + "/environments/dev-1", "displayName": "Development", "type": "DEV"},
            {"name": product + "/environments/prod-1", "displayName": "Production", "type": "PROD"},
        ]}
        self.platform.methods["EnvironmentsService/GenerateEnvironmentCredentials"] = lambda body: {
            "dotenv": base64.b64encode(b"SEARCH_ENABLED=\"true\"\nAPI_KEY=\"s3cret-value\"\n").decode(),
            "serviceAccountKey": base64.b64encode(b'{"private_key": "k3y-value"}').decode(),
        }

    def test_the_values_go_to_a_private_file_and_only_names_are_printed(self):
        code, out, err = self.machine.run("fetch_envs.py", "alis.os", "development")
        self.assertEqual(code, 0, err)
        env, key = self.repo / ".alis" / ".env", self.repo / ".alis" / "key.json"
        self.assertIn("API_KEY=\"s3cret-value\"", env.read_text())
        self.private(env)
        self.private(key)
        self.assertIn("SEARCH_ENABLED, API_KEY", out)
        self.assertNotIn("s3cret-value", out + err)
        self.assertNotIn("k3y-value", out + err)
        self.assertIn("/.alis/", (self.repo / ".gitignore").read_text())
        asked = self.platform.called("EnvironmentsService/GenerateEnvironmentCredentials")[0]
        self.assertEqual(asked["environment"], "organisations/alis/products/os/environments/dev-1")
        self.assertEqual(asked["credentialsPath"], str(key))
        self.assertIn('set -a; . "%s"; set +a' % env, out)

    def test_production_waits_for_the_persons_word(self):
        code, out, err = self.machine.run("fetch_envs.py", "alis.os", "Production")
        self.assertEqual(code, 1)
        self.assertIn("production environment", err)
        self.assertEqual(self.platform.called("EnvironmentsService/GenerateEnvironmentCredentials"), [])
        self.assertFalse((self.repo / ".alis").exists())
        code, out, err = self.machine.run("fetch_envs.py", "alis.os", "Production", "--production")
        self.assertEqual(code, 0, err)

    def test_an_unknown_environment_names_the_ones_there_are(self):
        code, out, err = self.machine.run("fetch_envs.py", "alis.os", "Staging")
        self.assertEqual(code, 1)
        self.assertIn("Development (dev-1)", err)


class InstallingPackages(ScriptTest):
    def setUp(self):
        super().setUp()
        self.machine.connect()
        self.folder = self.machine.workspace / "alis" / "build" / "os" / "users" / "v1"
        self.machine.write(self.folder, {
            "go.mod": "module alis.os.users.v1\n\nrequire (\n\talis.build/alis/os/users v1.3.0\n\talis.build/alis/os/iam/v2 v2.1.0\n)\n",
            "web/package.json": '{"dependencies": {"left-pad": "1.0.0"}}',
            "web/node_modules/dep/package.json": "{}",
            "tool/Cargo.toml": "[package]\n",
        })
        self.platform.methods["ProductsService/GenerateRegistryCredentials"] = lambda body: {
            "accessToken": "registry-token", "hosts": [
                {"registry": "GO", "host": "https://europe-west1-go.pkg.dev/proj/define-go"},
                {"registry": "NPM", "host": "europe-west1-npm.pkg.dev/proj/define-ecmascript/"},
                {"registry": "DART", "host": "https://dart.example", "token": "dart-token"},
            ]}

        def scripts(body):
            return {"scripts": [{
                "workingDirectory": location["workingDirectory"], "language": location["language"],
                "installPackages": "echo install >> ran.txt", "upgradeDefined": "echo upgrade-%s >> ran.txt" % (body.get("definedVersion") or "latest"),
                "addDefined": "echo add >> ran.txt", "upgradePackages": "echo all >> ran.txt",
            } for location in body["locations"]]}
        self.platform.methods["NeuronsService/GeneratePackageScripts"] = scripts

    def test_it_signs_in_to_the_registries_and_runs_what_alisx_generated(self):
        (self.machine.home / ".netrc").write_text("machine other.example\nlogin me\npassword mine\n")
        code, out, err = self.machine.run("install_packages.py", "alis.os.users.v1", "--version", "1.4.0")
        self.assertEqual(code, 0, out + err)
        asked = self.platform.called("NeuronsService/GeneratePackageScripts")[0]
        self.assertEqual(asked["neuron"], SERVICE)
        self.assertEqual(asked["definedVersion"], "1.4.0")
        found = {(Path(l["workingDirectory"]).name, l["language"]): l["usesScopedDefinedPackage"] for l in asked["locations"]}
        # node_modules is never walked, and Rust is left to the CLI.
        self.assertEqual(found, {("v1", "GO"): True, ("web", "NODE"): False})
        # The Go manifest names the service's own package: install, then that package at the version.
        self.assertEqual((self.folder / "ran.txt").read_text().split(), ["install", "upgrade-1.4.0"])
        self.assertEqual((self.folder / "web" / "ran.txt").read_text().split(), ["install"])
        netrc = (self.machine.home / ".netrc").read_text()
        self.assertIn("machine europe-west1-go.pkg.dev\nlogin oauth2accesstoken\npassword registry-token", netrc)
        self.assertIn("machine other.example\nlogin me\npassword mine", netrc)
        self.private(self.machine.home / ".netrc")
        self.assertIn("//europe-west1-npm.pkg.dev/proj/define-ecmascript/:_authToken=registry-token", (self.machine.home / ".npmrc").read_text())
        self.assertIn("Left alone (Rust)", out)
        self.assertNotIn("registry-token", out + err)

    def test_a_second_run_replaces_the_registry_entry(self):
        self.machine.run("install_packages.py", "alis.os.users.v1")
        self.machine.run("install_packages.py", "alis.os.users.v1")
        self.assertEqual((self.machine.home / ".netrc").read_text().count("machine europe-west1-go.pkg.dev"), 1)
        self.assertEqual((self.machine.home / ".npmrc").read_text().count("_authToken"), 1)

    def test_a_failing_command_fails_the_run_and_says_where(self):
        self.platform.methods["NeuronsService/GeneratePackageScripts"] = lambda body: {"scripts": [
            {"workingDirectory": l["workingDirectory"], "language": l["language"], "installPackages": "exit 7"} for l in body["locations"]]}
        code, out, err = self.machine.run("install_packages.py", "alis.os.users.v1", "--language", "go")
        self.assertEqual(code, 1)
        self.assertIn("Failed in: Go in .", err)

    def test_a_service_that_is_not_cloned_says_to_clone(self):
        code, out, err = self.machine.run("install_packages.py", "alis.os.mail.v1")
        self.assertEqual(code, 1)
        self.assertIn("git.py clone alis.os", err)

    def test_an_api_without_the_method_is_said_plainly(self):
        del self.platform.methods["NeuronsService/GeneratePackageScripts"]
        code, out, err = self.machine.run("install_packages.py", "alis.os.users.v1")
        self.assertEqual(code, 1)
        self.assertIn("does not have NeuronsService.GeneratePackageScripts yet", err)


class CreatingABlock(ScriptTest):
    def setUp(self):
        super().setUp()
        self.machine.connect()
        self.folder = self.machine.workspace / "alis" / "build" / "os" / "users" / "v1"
        self.machine.write(self.folder, {
            "main.go": "package main\n", "infra/main.tf": "# terraform\n", ".env": "SECRET=1\n", ".env.local": "SECRET=2\n",
            "key.json": "{}", "node_modules/x/index.js": "x", ".alis/.env": "SECRET=3\n", "big.bin": b"0" * (600 << 10),
        })
        self.machine.write(self.machine.workspace / "alis" / "define" / "alis" / "os" / "users" / "v1", {"users.proto": 'syntax = "proto3";\n'})
        self.platform.methods["BlocksService/CreateBlock"] = lambda body: {"block": "blocks/users", "instance": "blocks/users/instances/a1"}
        self.args = ["users", "alis.os.users.v1", "--account", "accounts/acme", "--name", "Users"]

    def test_nothing_is_sent_until_confirmed(self):
        code, out, err = self.machine.run("create_block.py", *self.args)
        self.assertEqual(code, 0, err)
        self.assertIn("Nothing was sent", out)
        self.assertIn("skipped, over 512 KiB: build/big.bin", out)
        self.assertEqual(self.platform.called("BlocksService/CreateBlock"), [])

    def test_confirmed_it_sends_the_services_files_and_no_secrets(self):
        code, out, err = self.machine.run("create_block.py", *self.args, "--confirm")
        self.assertEqual(code, 0, err)
        sent = self.platform.called("BlocksService/CreateBlock")[0]
        self.assertEqual((sent["neuron"], sent["blockId"], sent["displayName"], sent["account"]), (SERVICE, "users", "Users", "accounts/acme"))
        self.assertEqual(sorted(f["filename"] for f in sent["buildFiles"]), [".skill/SKILL.md", "main.go"])
        self.assertEqual([f["filename"] for f in sent["infraFiles"]], ["main.tf"])
        self.assertEqual([f["filename"] for f in sent["protoFiles"]], ["users.proto"])
        content = {f["filename"]: base64.b64decode(f["content"]) for f in sent["buildFiles"]}
        self.assertEqual(content["main.go"], b"package main\n")
        self.assertNotIn("SECRET", json.dumps(sent))
        self.assertIn("blocks/users/instances/a1", out)

    def test_a_bad_block_id_is_refused_before_anything_is_read(self):
        code, out, err = self.machine.run("create_block.py", "Users!", "alis.os.users.v1", "--account", "accounts/acme", "--name", "Users")
        self.assertEqual(code, 2)


class Git(ScriptTest):
    def setUp(self):
        super().setUp()
        self.machine.connect()
        self.machine.remote("alis", "proto", {"alis/os/users/v1/users.proto": 'syntax = "proto3";\n'})
        self.machine.remote("alis", "os", {"users/v1/main.go": "package main\n", "other/v1/keep.txt": "keep\n"})
        self.build = self.machine.workspace / "alis" / "build" / "os"
        self.define = self.machine.workspace / "alis" / "define"

    def test_clone_pull_and_push(self):
        code, out, err = self.machine.run("git.py", "clone", "alis.os")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.build / "users" / "v1" / "main.go").exists())
        self.assertTrue((self.define / "alis" / "os" / "users" / "v1" / "users.proto").exists())
        # Nothing of the sign-in is left in the clone's own settings.
        self.assertNotIn("helper", self.machine.git(self.build, "config", "--local", "--list"))

        # Someone else pushes; pull brings it in.
        seed = self.machine.root / "seed-alis-os"
        self.machine.write(seed, {"users/v1/theirs.go": "package main\n"})
        self.machine.git(seed, "add", "-A")
        self.machine.git(seed, "commit", "--quiet", "-m", "theirs")
        self.machine.git(seed, "push", "--quiet", "origin", "HEAD:main")
        code, out, err = self.machine.run("git.py", "pull", cwd=self.build / "users")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.build / "users" / "v1" / "theirs.go").exists())

        # A commit is pushed, and the commit is named for the connector's next step.
        self.machine.write(self.build, {"users/v1/mine.go": "package main\n", "users/v1/unsaved.go": "x"})
        self.machine.git(self.build, "add", "users/v1/mine.go")
        self.machine.git(self.build, "commit", "--quiet", "-m", "mine")
        code, out, err = self.machine.run("git.py", "push", cwd=self.build)
        self.assertEqual(code, 0, err)
        commit = self.machine.git(self.build, "rev-parse", "HEAD").strip()
        self.assertIn("Commit: " + commit, out)
        self.assertIn("1 changed file(s) are not committed", out)
        self.assertIn("`build`", out)
        self.assertEqual(self.machine.git(self.machine.remotes / "alis" / "os.git", "rev-parse", "main").strip(), commit)

        # Cloning again pulls and does not fail.
        code, out, err = self.machine.run("git.py", "clone", "alis.os")
        self.assertEqual(code, 0, err)
        self.assertIn("Already cloned", out)

    def test_git_is_handed_the_sign_in_for_one_command_and_no_other_helper_answers(self):
        # A clone the AlisX CLI set up has a helper of its own in its settings.
        self.machine.run("git.py", "clone", "alis.os")
        self.machine.git(self.build, "config", "credential.https://git.alis.alis.dev.helper", "!echo password=from-another-helper")
        asked = subprocess.run(
            [sys.executable, "-c",
             "import subprocess, sys; sys.path.insert(0, %r); import _alisx\n"
             "done = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\\nhost=git.alis.alis.dev\\n\\n',"
             " env=_alisx.git_environment(True), stdout=subprocess.PIPE, universal_newlines=True)\n"
             "print(done.stdout)" % str(SCRIPTS)],
            cwd=str(self.build), env=self.machine.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        self.assertEqual(asked.returncode, 0, asked.stderr)
        self.assertIn("username=alisx-plugin", asked.stdout)
        self.assertRegex(asked.stdout, r"password=\S+\.\S+\.signature")
        self.assertNotIn("from-another-helper", asked.stdout)

    def test_a_push_the_remote_is_ahead_of_is_not_forced(self):
        self.machine.run("git.py", "clone", "alis.os")
        seed = self.machine.root / "seed-alis-os"
        self.machine.write(seed, {"users/v1/theirs.go": "package main\n"})
        self.machine.git(seed, "add", "-A")
        self.machine.git(seed, "commit", "--quiet", "-m", "theirs")
        self.machine.git(seed, "push", "--quiet", "origin", "HEAD:main")
        self.machine.write(self.build, {"users/v1/mine.go": "package main\n"})
        self.machine.git(self.build, "add", "-A")
        self.machine.git(self.build, "commit", "--quiet", "-m", "mine")
        code, out, err = self.machine.run("git.py", "push", cwd=self.build)
        self.assertEqual(code, 1)
        self.assertIn("nothing was forced", err)


class MergingABlock(ScriptTest):
    BRANCH = "block/simpleapi/alis.os.users.v1/a1"

    def setUp(self):
        super().setUp()
        self.machine.connect()
        self.machine.remote("alis", "proto", {"alis/os/users/v1/users.proto": 'syntax = "proto3";\n'})
        seed = self.machine.remote("alis", "os", {"users/v1/main.go": "package main\n", "other/v1/keep.txt": "keep\n"})
        # AlisX writes the block's files to a branch that also drops what lies
        # outside the service's folder.
        self.machine.git(seed, "checkout", "--quiet", "-b", self.BRANCH)
        self.machine.git(seed, "rm", "--quiet", "-r", "other")
        self.machine.write(seed, {"users/v1/block.go": "package main // from the block\n"})
        self.machine.git(seed, "add", "-A")
        self.machine.git(seed, "commit", "--quiet", "-m", "block files")
        self.machine.git(seed, "push", "--quiet", "origin", self.BRANCH)
        self.machine.run("git.py", "clone", "alis.os")
        self.build = self.machine.workspace / "alis" / "build" / "os"
        self.platform.methods["BlocksService/ListBlockInstances"] = lambda body: {"blockInstances": [
            {"name": "blocks/simpleapi/instances/a1", "neuron": SERVICE, "state": "PENDING_INSTALLATION", "gitBranch": self.BRANCH}]}

    def test_the_block_reaches_main_and_nothing_outside_its_folder_is_lost(self):
        # The person is in the middle of something: it must be left as it is.
        self.machine.write(self.build, {"users/v1/wip.txt": "half done\n"})
        code, out, err = self.machine.run("merge_block.py", "simpleapi", "alis.os.users.v1")
        self.assertEqual(code, 0, out + err)
        asked = self.platform.called("BlocksService/ListBlockInstances")[0]
        self.assertEqual((asked["parent"], asked["filter"]), ("blocks/simpleapi", 'neuron = "%s"' % SERVICE))
        remote = self.machine.remotes / "alis" / "os.git"
        on_main = self.machine.git(remote, "ls-tree", "-r", "--name-only", "main").split()
        self.assertEqual(sorted(on_main), ["other/v1/keep.txt", "users/v1/block.go", "users/v1/main.go"])
        self.assertIn("Kept 1 file(s) outside users/v1/", out)
        # The definitions repo has no such branch: said, and not a failure.
        self.assertIn("nothing to merge there", out)
        # The checkout was moved up to the merge, with the unfinished file untouched.
        self.assertTrue((self.build / "users" / "v1" / "block.go").exists())
        self.assertEqual((self.build / "users" / "v1" / "wip.txt").read_text(), "half done\n")
        self.assertEqual(self.machine.git(self.build, "worktree", "list").count("\n"), 1)

    def test_a_second_run_finds_nothing_left_to_merge(self):
        self.machine.run("merge_block.py", "simpleapi", "alis.os.users.v1")
        code, out, err = self.machine.run("merge_block.py", "simpleapi", "alis.os.users.v1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Nothing was left to merge", out)

    def test_a_conflict_inside_the_blocks_folder_changes_nothing(self):
        seed = self.machine.root / "seed-alis-os"
        self.machine.git(seed, "checkout", "--quiet", "main")
        self.machine.write(seed, {"users/v1/block.go": "package main // ours\n"})
        self.machine.git(seed, "add", "-A")
        self.machine.git(seed, "commit", "--quiet", "-m", "ours")
        self.machine.git(seed, "push", "--quiet", "origin", "HEAD:main")
        before = self.machine.git(self.machine.remotes / "alis" / "os.git", "rev-parse", "main")
        code, out, err = self.machine.run("merge_block.py", "simpleapi", "alis.os.users.v1")
        self.assertEqual(code, 1)
        self.assertIn("No repo was changed", err)
        self.assertIn("settled by hand", err)
        self.assertEqual(self.machine.git(self.machine.remotes / "alis" / "os.git", "rev-parse", "main"), before)

    def test_without_a_branch_from_alisx_it_is_found_on_the_remote(self):
        self.platform.methods["BlocksService/ListBlockInstances"] = lambda body: {"blockInstances": []}
        code, out, err = self.machine.run("merge_block.py", "simpleapi", "alis.os.users.v1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Merging " + self.BRANCH, out)

    @unittest.skipUnless(shutil.which("git-lfs"), "git-lfs is not installed")
    def test_a_file_the_repo_keeps_in_git_lfs_reaches_main_as_a_pointer(self):
        # Main says images go through Git LFS; AlisX wrote the block's image plain.
        seed = self.machine.root / "seed-alis-os"
        self.machine.git(seed, "checkout", "--quiet", "main")
        self.machine.write(seed, {".gitattributes": "*.png filter=lfs diff=lfs merge=lfs -text\n"})
        self.machine.git(seed, "add", "-A")
        self.machine.git(seed, "commit", "--quiet", "-m", "images in lfs")
        self.machine.git(seed, "push", "--quiet", "origin", "HEAD:main")
        self.machine.git(seed, "checkout", "--quiet", self.BRANCH)
        self.machine.write(seed, {"users/v1/logo.png": b"\x89PNG\r\n\x1a\n" + b"\x00\xff" * 64})
        self.machine.git(seed, "add", "-A")
        self.machine.git(seed, "commit", "--quiet", "-m", "block image")
        self.machine.git(seed, "push", "--quiet", "origin", self.BRANCH)
        code, out, err = self.machine.run("merge_block.py", "simpleapi", "alis.os.users.v1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("with Git LFS", out)
        stored = self.machine.git(self.machine.remotes / "alis" / "os.git", "cat-file", "blob", "main:users/v1/logo.png")
        self.assertTrue(stored.startswith("version https://git-lfs.github.com/spec/"), stored[:80])

    def test_refused_by_alisx_is_said_and_nothing_is_merged(self):
        def refuse(body):
            raise Refused("permission_denied", "not a builder of alis.os", 403)
        self.platform.methods["BlocksService/ListBlockInstances"] = refuse
        code, out, err = self.machine.run("merge_block.py", "simpleapi", "alis.os.users.v1")
        self.assertEqual(code, 1)
        self.assertIn("not a builder of alis.os", err)


if __name__ == "__main__":
    unittest.main()
