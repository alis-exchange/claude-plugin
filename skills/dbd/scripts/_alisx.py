"""Shared code of the AlisX plugin's scripts. Standard library only.

Every script signs in the same way. connect.py keeps a sign-in on this
machine, and each call here trades it for a short-lived token for the AlisX
API (api.alisx.com). That token is also the password git is given.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The API's name in a token. It is the same in every environment: the address
# the API answers at differs (api.alisx.com, api.alisx.dev), its name does not.
RESOURCE = "https://api.alisx.com"
# What the scripts ask a token for: build work, and nothing else the sign-in
# could carry.
SCOPE = "build:read build:write"
DEFAULT_IDENTITY = "https://identity.alisx.com"
API_PACKAGE = "alis.os.gateway.v1"
TIMEOUT = 120

EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NOT_CONNECTED = 3

NOT_CONNECTED = (
    "Not connected: this machine has no AlisX sign-in. Call the AlisX connector's "
    "`connect` tool, run the command its answer gives (connect.py <code> ...), "
    "then run this again."
)


class Stop(Exception):
    """A failure to tell the person about: what happened and what to do."""

    def __init__(self, message: str, code: int = EXIT_FAILED):
        super().__init__(message)
        self.code = code


class NotConnected(Stop):
    def __init__(self, message: str = NOT_CONNECTED):
        super().__init__(message, EXIT_NOT_CONNECTED)


class ApiError(Stop):
    """The API refused a call. `status` is its word for why (not_found, ...)."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def run(main) -> None:
    """Run a script's main and turn a Stop into one plain line and an exit code."""
    try:
        code = main()
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        sys.exit(stop.code)
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(code or 0)


def say(line: str = "") -> None:
    print(line, flush=True)


# --- the sign-in kept on this machine ---------------------------------------


def home() -> Path:
    """Where the plugin keeps its sign-in: apart from the AlisX CLI's own."""
    override = os.environ.get("ALISX_PLUGIN_HOME")
    return Path(override) if override else Path.home() / ".alisx" / "plugin"


def write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read, whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _sign_in_path() -> Path:
    return home() / "sign-in.json"


def _token_path() -> Path:
    return home() / "token.json"


def load_sign_in() -> dict:
    try:
        sign_in = json.loads(_sign_in_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise NotConnected()
    if not sign_in.get("refresh_token") or not sign_in.get("client_id"):
        raise NotConnected()
    return sign_in


def save_sign_in(sign_in: dict) -> None:
    directory = home()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    write_private(_sign_in_path(), json.dumps(sign_in, indent=2) + "\n")


def forget_sign_in() -> bool:
    """Remove the sign-in from this machine. True when there was one."""
    removed = False
    for path in (_sign_in_path(), _token_path()):
        try:
            path.unlink()
            removed = True
        except OSError:
            pass
    return removed


def api_address(identity: str) -> str:
    """The API's address for an identity service: each environment has its own."""
    override = os.environ.get("ALISX_API_URL")
    if override:
        return override.rstrip("/")
    host = urllib.parse.urlparse(identity).hostname or ""
    return "https://api.alisx.dev" if host.endswith(".alisx.dev") else "https://api.alisx.com"


# --- talking to the identity service and the API ----------------------------


def _unreachable(url: str, reason) -> Stop:
    host = urllib.parse.urlparse(url).hostname or url
    return Stop(
        "Could not reach %s (%s). Nothing was changed. In a cloud session the "
        "environment's allowed domains must include it; otherwise check the network "
        "and run this again." % (host, reason)
    )


def post_json(url: str, body: dict, headers: dict | None = None):
    """POST JSON, answer (HTTP status, parsed body). A refusal is an answer too."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "accept": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as refused:
        raw, status = refused.read(), refused.code
    except (urllib.error.URLError, OSError) as failed:
        raise _unreachable(url, getattr(failed, "reason", failed))
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        parsed = {"message": raw.decode("utf-8", "replace")[:500]}
    return status, parsed if isinstance(parsed, dict) else {"value": parsed}


def claims(token: str) -> dict:
    """What a signed token says of itself. Read, never trusted: the API checks it."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


def _token_refusal(answer: dict) -> Stop:
    error = answer.get("error", "")
    said = answer.get("error_description") or answer.get("message") or error or "no reason given"
    if error in ("invalid_target", "invalid_scope"):
        return Stop(
            "AlisX would not give this sign-in a token for its API (%s). The sign-in "
            "the connector handed out does not carry build access; tell the person." % said
        )
    if error in ("invalid_grant", "invalid_client"):
        return NotConnected(
            "This machine's AlisX sign-in is no longer good (%s). Call the AlisX "
            "connector's `connect` tool again and run the command its answer gives." % said
        )
    return Stop("AlisX's identity service refused the sign-in (%s)." % said)


def redeem(identity: str, client_id: str, code: str) -> dict:
    """Trade the connector's one-time code for a sign-in, and keep it."""
    identity = identity.rstrip("/")
    status, answer = post_json(
        identity + "/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "offline",
            "client_id": client_id,
            "resource": RESOURCE,
        },
    )
    if status != 200 or not answer.get("refresh_token"):
        if answer.get("error") == "invalid_grant":
            raise Stop(
                "AlisX did not take the code (%s). A code is good once and for ten "
                "minutes: call the connector's `connect` tool for a new one."
                % (answer.get("error_description") or "invalid_grant")
            )
        raise _token_refusal(answer)
    sign_in = {
        "identity": identity,
        "client_id": client_id,
        "api": api_address(identity),
        "refresh_token": answer["refresh_token"],
    }
    save_sign_in(sign_in)
    try:
        _token_path().unlink()
    except OSError:
        pass
    return sign_in


def access_token(fresh: bool = False, need: int = 120) -> str:
    """A token for the API, good for at least `need` more seconds."""
    sign_in = load_sign_in()
    if not fresh:
        try:
            kept = json.loads(_token_path().read_text(encoding="utf-8"))
            if kept.get("for") == sign_in["refresh_token"][-12:] and kept.get("exp", 0) - time.time() > need:
                return kept["access_token"]
        except (OSError, ValueError, KeyError):
            pass
    status, answer = post_json(
        sign_in["identity"] + "/token",
        {
            "grant_type": "refresh_token",
            "refresh_token": sign_in["refresh_token"],
            "client_id": sign_in["client_id"],
            "resource": RESOURCE,
            "scope": SCOPE,
        },
    )
    token = answer.get("access_token")
    if status != 200 or not token:
        raise _token_refusal(answer)
    # A sign-in whose refresh token is replaced on use hands the next one back.
    replaced = answer.get("refresh_token")
    if replaced and replaced != sign_in["refresh_token"]:
        sign_in["refresh_token"] = replaced
        save_sign_in(sign_in)
    expires = claims(token).get("exp") or (time.time() + int(answer.get("expires_in") or 300))
    write_private(
        _token_path(),
        json.dumps({"access_token": token, "exp": expires, "for": sign_in["refresh_token"][-12:]}),
    )
    return token


def api(service: str, method: str, body: dict | None = None) -> dict:
    """Call one method of the AlisX API as the person, in JSON."""
    sign_in = load_sign_in()
    url = "%s/%s.%s/%s" % (sign_in.get("api") or api_address(sign_in["identity"]), API_PACKAGE, service, method)
    answer: dict = {}
    for attempt in (1, 2):
        token = access_token(fresh=attempt == 2)
        status, answer = post_json(url, body or {}, {"authorization": "Bearer " + token, "connect-protocol-version": "1"})
        if status == 200:
            return answer
        # A token that ran out between the check and the call: once more, fresh.
        if status == 401 and attempt == 1:
            continue
        break
    word = str(answer.get("code") or status)
    said = answer.get("message") or "no reason given"
    if word == "unauthenticated":
        raise NotConnected(
            "The AlisX API did not take this machine's sign-in (%s). Call the AlisX "
            "connector's `connect` tool again and run the command its answer gives." % said
        )
    if word == "permission_denied":
        raise ApiError(word, "AlisX refused %s.%s: %s. The person signed in may not do this." % (service, method, said))
    if word == "unimplemented" or status == 404:
        raise ApiError(
            "unimplemented",
            "The AlisX API here does not have %s.%s yet (%s). The plugin is newer than "
            "the API it reached; tell the person." % (service, method, said),
        )
    raise ApiError(word, "AlisX refused %s.%s: %s" % (service, method, said))


# --- names and folders -------------------------------------------------------


class Service:
    """A service by its package id: <build environment>.<product>.<path...>.v<N>."""

    def __init__(self, package: str):
        parts = package.strip().split(".")
        last = parts[-1] if parts else ""
        if len(parts) < 4 or not all(parts) or not (last[:1] == "v" and last[1:].isdigit()):
            raise Stop(
                "%r is not a package id. A service is named <build environment>."
                "<product>.<service>.v<N>, for example alis.os.users.v1." % package,
                EXIT_USAGE,
            )
        self.package = ".".join(parts)
        self.organisation, self.product = parts[0], parts[1]
        self.path = parts[2:]

    @property
    def neuron(self) -> str:
        return "-".join(self.path)

    @property
    def product_name(self) -> str:
        return product_name(self.organisation, self.product)

    @property
    def name(self) -> str:
        return "%s/neurons/%s" % (self.product_name, self.neuron)

    @property
    def build_folder(self) -> Path:
        return build_root(self.organisation, self.product).joinpath(*self.path)

    @property
    def define_folder(self) -> Path:
        return define_root(self.organisation).joinpath(self.organisation, self.product, *self.path)


def product_name(organisation: str, product: str) -> str:
    return "organisations/%s/products/%s" % (organisation, product)


def split_product(given: str):
    """<build environment>.<product>, as `alis.os`."""
    parts = given.strip().split(".")
    if len(parts) != 2 or not all(parts):
        raise Stop(
            "%r is not a product. Name it <build environment>.<product>, for example alis.os." % given,
            EXIT_USAGE,
        )
    return parts[0], parts[1]


def workspace_root() -> Path:
    """Where products are cloned: the AlisX CLI's own folder, so both see one tree."""
    root = os.environ.get("ALIS_WORKSPACE_ROOT", "")
    return Path(root) if os.path.isabs(root) else Path.home() / "alis.build"


def build_root(organisation: str, product: str) -> Path:
    return workspace_root() / organisation / "build" / product


def define_root(organisation: str) -> Path:
    return workspace_root() / organisation / "define"


def service_name(service: Service) -> str:
    """The service's resource name, as the API lists it (its id is not always its path)."""
    page = ""
    while True:
        answer = api("NeuronsService", "ListNeurons", {"parent": service.product_name, "pageSize": 200, "pageToken": page})
        for neuron in answer.get("neurons", []):
            if neuron.get("packageId") == service.package:
                return neuron["name"]
        page = answer.get("nextPageToken") or ""
        if not page:
            break
    raise Stop(
        "AlisX has no service %s in the product %s.%s. Check the package id, or create "
        "the service with the connector first." % (service.package, service.organisation, service.product)
    )


# --- git, with the person's sign-in for this one command ---------------------

# The helper git asks for a password. It reads the token from the environment
# of the one git command it was started for: nothing is written to git's
# config, a credential store or the command line.
_HELPER = '!f() { test "$1" = get && printf "username=%s\\npassword=%s\\n" "$ALISX_GIT_USER" "$ALISX_GIT_TOKEN"; }; f'


def git_environment(remote: bool) -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not remote:
        return env
    settings = [
        ("credential.helper", ""),  # no stored helper answers before ours
        ("credential.helper", _HELPER),
        ("credential.useHttpPath", "false"),
    ]
    start = int(env.get("GIT_CONFIG_COUNT") or 0)
    for index, (key, value) in enumerate(settings, start):
        env["GIT_CONFIG_KEY_%d" % index] = key
        env["GIT_CONFIG_VALUE_%d" % index] = value
    env["GIT_CONFIG_COUNT"] = str(start + len(settings))
    env["ALISX_GIT_USER"] = "alisx-plugin"
    # Long enough for a clone or a push of some size.
    env["ALISX_GIT_TOKEN"] = access_token(need=240)
    return env


class GitFailed(Stop):
    def __init__(self, args, output: str):
        super().__init__("git %s failed:\n%s" % (" ".join(args), output.strip()))
        self.output = output


def git_bytes(directory, *args, remote: bool = False, stdin: str | None = None, stdout_only: bool = True) -> bytes:
    """Run git in a folder and answer its output as it came, byte for byte."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(directory),
            env=git_environment(remote),
            input=None if stdin is None else stdin.encode("utf-8", "surrogateescape"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if stdout_only else subprocess.STDOUT,
        )
    except FileNotFoundError:
        raise Stop("git is not installed on this machine. Install git and run this again.")
    if done.returncode != 0:
        raise GitFailed(args, ((done.stderr if stdout_only else done.stdout) or b"").decode("utf-8", "replace"))
    return done.stdout


def git(directory, *args, remote: bool = False, stdin: str | None = None, stdout_only: bool = False) -> str:
    """Run git in a folder and answer what it printed. `remote` signs it in.

    `stdout_only` leaves warnings out of the answer, for output that is parsed.
    """
    out = git_bytes(directory, *args, remote=remote, stdin=stdin, stdout_only=stdout_only)
    return out.decode("utf-8", "surrogateescape")


def git_ok(directory, *args, remote: bool = False) -> bool:
    try:
        git(directory, *args, remote=remote)
        return True
    except GitFailed:
        return False


def git_host(organisation: str) -> str:
    """A build environment's git host. Its repos: `proto`, and one per product."""
    template = os.environ.get("ALISX_GIT_URL") or "https://git.{organisation}.alis.dev/{organisation}"
    return template.format(organisation=organisation).rstrip("/")
