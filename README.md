# nuqql-slixmppd

nuqql-slixmppd is a network daemon that implements the nuqql interface and uses
[slixmpp](https://lab.louiz.org/poezio/slixmpp) to connect to XMPP chat
networks. It can be used as a backend for
[nuqql](https://github.com/hwipl/nuqql) or as a standalone chat client daemon.

nuqql-slixmppd is a fork of [nuqql-based](https://github.com/hwipl/nuqql-based)
that adds slixmpp for XMPP support. Thus,
[slixmpp](https://lab.louiz.org/poezio/slixmpp) is a requirement to run
nuqql-slixmppd. Another optional dependency is
[daemon](https://pypi.org/project/python-daemon/), that is needed to run
slixmppd in daemon mode.


## Quick Start

Make sure you have installed nuqql-slixmppd's dependencies:
* [slixmpp](https://lab.louiz.org/poezio/slixmpp): for XMPP (jabber) support
* [daemon](https://pypi.org/project/python-daemon/) (optional): for daemonize
  support

You can run nuqql-slixmppd by executing *slixmppd.py*, e.g., with
`./slixmppd.py`.

By default, it listens on TCP port 32000 on your local host. So, you can
connect with telnet to it, e.g., with `telnet localhost 32000`.

In the telnet session you can:
* add XMPP accounts with: `account add xmpp <username> <password>`.
* retrieve the list of accounts and their numbers/IDs with `account list`.
* retrieve your buddy list with `account <id> buddies`
* send a message to a user with `account <id> send <username> <message>`


## Usage

See `slixmppd.py --help` for a list of command line arguments:

```
usage: slixmppd.py [-h] [--af {inet,unix}] [--address ADDRESS] [--port PORT]
                   [--sockfile SOCKFILE] [--dir DIR] [-d]

Run nuqql backend.

optional arguments:
  -h, --help           show this help message and exit
  --af {inet,unix}     socket address family: "inet" for AF_INET, "unix" for
                       AF_UNIX
  --address ADDRESS    AF_INET listen address
  --port PORT          AF_INET listen port
  --sockfile SOCKFILE  AF_UNIX socket file in DIR
  --dir DIR            working directory
  -d, --daemonize      daemonize process
```

## Changes

* v0.3:
  * Add group chat support and messages:
    * list chats on account: `account <id> chat list`
    * join a chat on account: `account <id> chat join <chat>`
    * part a chat on account: `account <id> chat part <chat>`
    * send a message to a chat on account:
      `account <id> chat send <chat> <message>`
    * list users of a chat on account: `account <id> chat users <chat>`
  * Cleanups, fixes, and improvements
* v0.2:
  * Add account status message:
    * Set current status with: `account <id> status set <status>`
    * Get current status with: `account <id> status get`
  * Use stricter permissions for account, log, and sock files
* v0.1:
  * First/initial release.
