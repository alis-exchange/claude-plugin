"""A stand-in for AlisX's identity service and API, and a throwaway machine.

The scripts are run as they are run for real, as separate processes, against
this server and a temporary home folder. Nothing here reaches the network.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "dbd" / "scripts"
PREFIX = "/alis.os.gateway.v1."


def token(email: str = "builder@example.com", seconds: int = 600) -> str:
    """A token shaped like AlisX's: only its expiry and its claims are read."""
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    return "%s.%s.signature" % (part({"typ": "at+jwt"}), part({"exp": int(time.time()) + seconds, "email": email}))


class Platform:
    """The fake identity service and API, on one local port."""

    def __init__(self):
        self.codes = {"good-code": "refresh-1"}
        self.refresh_tokens = {"refresh-1"}
        self.token_requests = []
        self.calls = []
        self.methods = {"IdentityService/RetrieveMe": lambda body: {"user": "users/1", "email": "builder@example.com", "displayName": "A Builder"}}
        platform = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, status: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                if self.path == "/token":
                    platform.token_requests.append(body)
                    if body.get("grant_type") == "authorization_code":
                        refresh = platform.codes.pop(body.get("code"), None)
                        if not refresh or body.get("redirect_uri") != "offline":
                            return self.answer(400, {"error": "invalid_grant", "error_description": "auth code not found"})
                        return self.answer(200, {"access_token": token(), "refresh_token": refresh})
                    if body.get("refresh_token") in platform.refresh_tokens:
                        return self.answer(200, {"access_token": token()})
                    return self.answer(400, {"error": "invalid_grant", "error_description": "refresh token not found"})
                if self.path.startswith(PREFIX):
                    if not self.headers.get("authorization", "").startswith("Bearer "):
                        return self.answer(401, {"code": "unauthenticated", "message": "no token"})
                    name = self.path[len(PREFIX):]
                    platform.calls.append((name, body))
                    method = platform.methods.get(name)
                    if not method:
                        return self.answer(404, {"code": "unimplemented", "message": "unknown method"})
                    try:
                        return self.answer(200, method(body))
                    except Refused as refused:
                        return self.answer(refused.status, {"code": refused.code, "message": str(refused)})
                self.answer(404, {"message": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def called(self, name: str) -> list:
        return [body for called, body in self.calls if called == name]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Refused(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


class Machine:
    """A temporary home, workspace and git remotes for the scripts to work in."""

    def __init__(self, platform: Platform):
        self.platform = platform
        self.temp = tempfile.TemporaryDirectory(prefix="alisx-plugin-test-")
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.workspace = self.root / "workspace"
        self.remotes = self.root / "remotes"
        for folder in (self.home, self.workspace, self.remotes):
            folder.mkdir()
        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "ALISX_PLUGIN_HOME": str(self.home / ".alisx" / "plugin"),
            "ALISX_API_URL": platform.address,
            "ALIS_WORKSPACE_ROOT": str(self.workspace),
            "ALISX_GIT_URL": str(self.remotes) + "/{organisation}",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig"),
            "GIT_AUTHOR_NAME": "A Builder", "GIT_AUTHOR_EMAIL": "builder@example.com",
            "GIT_COMMITTER_NAME": "A Builder", "GIT_COMMITTER_EMAIL": "builder@example.com",
        })
        for name in ("GIT_CONFIG_COUNT", "ALIS_DEV"):
            self.env.pop(name, None)

    def run(self, script: str, *args, cwd=None):
        """Run one of the plugin's scripts; answer (exit code, stdout, stderr)."""
        done = subprocess.run(
            [sys.executable, str(SCRIPTS / script), *[str(a) for a in args]],
            cwd=str(cwd or self.root), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        return done.returncode, done.stdout, done.stderr

    def connect(self) -> None:
        code, out, err = self.run("connect.py", "good-code", "--client-id", "the-claude-app", "--identity", self.platform.address)
        assert code == 0, out + err

    def git(self, folder, *args) -> str:
        return subprocess.run(
            ["git", *[str(a) for a in args]], cwd=str(folder), env=self.env, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
        ).stdout

    def remote(self, organisation: str, name: str, files: dict) -> Path:
        """A bare remote with one commit on main, holding the files given."""
        bare = self.remotes / organisation / (name + ".git")
        bare.parent.mkdir(parents=True, exist_ok=True)
        self.git(self.root, "init", "--quiet", "--bare", "--initial-branch=main", bare)
        seed = self.root / ("seed-" + organisation + "-" + name)
        self.git(self.root, "clone", "--quiet", bare, seed)
        self.write(seed, files)
        self.git(seed, "add", "-A")
        self.git(seed, "commit", "--quiet", "-m", "first")
        self.git(seed, "push", "--quiet", "origin", "HEAD:main")
        return seed

    @staticmethod
    def write(folder: Path, files: dict) -> None:
        for relative, content in files.items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")

    def close(self) -> None:
        self.temp.cleanup()
