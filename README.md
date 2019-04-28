# nuqql-slixmppd

nuqql-slixmppd is a network daemon that implements the nuqql interface and uses
[slixmpp](https://lab.louiz.org/poezio/slixmpp) to connect to XMPP chat
networks. It can be used as a backend for
[nuqql](https://github.com/hwipl/nuqql) or as a standalone chat client daemon.

nuqql-slixmppd is a fork of [nuqql-based](https://github.com/hwipl/nuqql-based)
that adds slixmpp for XMPP support. Thus,
[slixmpp](https://lab.louiz.org/poezio/slixmpp) is a requirement to run
nuqql-slixmppd.

You can run nuqql-slixmppd by executing *slixmppd.py*, e.g., with
`./slixmppd.py`.

By default, it listens on TCP port 32000 on your local host. So, you can
connect with telnet to it, e.g., with `telnet localhost 32000`.

In the telnet session you can:
* add XMPP accounts with: `account add xmpp <username> <password>`.
* retrieve the list of accounts and their numbers/IDs with `account list`.
* retrieve your buddy list with `account <id> buddies`
* send a message to a user with `account <id> send <username> <message>`


## Changes

* v0.2:
  * Add account status message:
    * Set current status with: `account <id> status set <status>`
    * Get current status with: `account <id> status get`
  * Use stricter permissions for account, log, and sock files
* v0.1:
  * First/initial release.
