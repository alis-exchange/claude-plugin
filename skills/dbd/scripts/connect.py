#!/usr/bin/env python3
"""Connect this machine to AlisX, for the plugin's scripts.

    connect.py <code> --client-id <id> [--identity <address>]
    connect.py --status
    connect.py --disconnect

The code comes from the AlisX connector's `connect` tool, which prints this
command whole. It is good once and for ten minutes. This script trades it for
a sign-in kept on this machine, where only the person can read it, and the
other scripts renew their own access from it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _alisx as alisx  # noqa: E402


def who() -> str:
    """Who the sign-in is, as the API says it. Proves the API takes the sign-in."""
    me = alisx.api("IdentityService", "RetrieveMe")
    return me.get("email") or me.get("displayName") or me.get("user") or "the person signed in"


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect this machine to AlisX for the plugin's scripts.")
    parser.add_argument("code", nargs="?", help="the one-time code the connector's `connect` tool answered with")
    parser.add_argument("--client-id", help="the client the code was made for, as the connector's answer gives it")
    parser.add_argument("--identity", default=alisx.DEFAULT_IDENTITY, help="the identity service's address (default %(default)s)")
    parser.add_argument("--status", action="store_true", help="say whether this machine is connected, and as whom")
    parser.add_argument("--disconnect", action="store_true", help="remove the sign-in from this machine")
    args = parser.parse_args()

    if args.disconnect:
        removed = alisx.forget_sign_in()
        alisx.say("Disconnected: the sign-in was removed from this machine." if removed else "This machine was not connected.")
        return 0

    if args.status:
        sign_in = alisx.load_sign_in()
        alisx.say("Connected as %s, through %s." % (who(), sign_in["identity"]))
        return 0

    if not args.code:
        parser.error("give the code from the connector's `connect` tool, or --status")
    if not args.client_id:
        parser.error("give --client-id as the connector's answer has it: run the command the answer prints, whole")

    alisx.redeem(args.identity, args.client_id, args.code)
    try:
        person = who()
    except alisx.NotConnected as refused:
        # A sign-in the API does not take is no sign-in: do not leave it behind.
        alisx.forget_sign_in()
        raise alisx.Stop(
            "The code was taken, but the AlisX API does not accept the sign-in it gave, "
            "so nothing was kept. (%s)" % refused
        )
    except alisx.Stop as unchecked:
        raise alisx.Stop(
            "The sign-in was kept, but it could not be checked against the AlisX API, so "
            "the scripts may not work yet. The code is used up: do not ask for another "
            "until the API can be reached. (%s)" % unchecked
        )
    alisx.say("Connected as %s. The plugin's scripts on this machine now work as them." % person)
    alisx.say("The code is used up. Do not repeat it; run the command that needed the machine again.")
    return 0


if __name__ == "__main__":
    alisx.run(main)
