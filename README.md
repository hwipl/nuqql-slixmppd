# nuqql-slixmppd

nuqql-slixmppd is a network daemon that implements the nuqql interface and uses
[slixmpp](https://codeberg.org/poezio/slixmpp) to connect to XMPP chat
networks. It can be used as a backend for
[nuqql](https://github.com/hwipl/nuqql) or as a standalone chat client daemon.

nuqql-slixmppd's dependencies are:
* [nuqql-based](https://github.com/hwipl/nuqql-based)
* [slixmpp](https://lab.louiz.org/poezio/slixmpp)
* [daemon](https://pypi.org/project/python-daemon/) (optional)


## Quick Start

You can install nuqql-slixmppd and its dependencies, for example, with pip for
your user only with the following command:

```console
$ pip install --user nuqql-slixmppd
```

After the installation, you can run nuqql-slixmppd by running the
`nuqql-slixmppd` command:

```console
$ nuqql-slixmppd
```

By default, it listens on TCP port 32000 on your local host. So, you can
connect with, e.g., telnet to it with the following command:

```console
$ telnet localhost 32000
```

In the telnet session you can:
* add XMPP accounts with: `account add xmpp <username> <password>`.
* retrieve the list of accounts and their numbers/IDs with `account list`.
* retrieve your buddy list with `account <id> buddies`
* send a message to a user with `account <id> send <username> <message>`


## Usage

See `nuqql-slixmppd --help` for a list of command line arguments:

```
usage: nuqql-slixmppd [--address ADDRESS] [--af {inet,unix}] [-d] [--dir DIR]
[--disable-history] [--filter-own] [-h] [--loglevel {debug,info,warn,error}]
[--port PORT] [--push-accounts] [--sockfile SOCKFILE] [--version]

Run nuqql backend slixmppd.

optional arguments:
  --address ADDRESS     set AF_INET listen address
  --af {inet,unix}      set socket address family: "inet" for AF_INET, "unix"
                        for AF_UNIX
  -d, --daemonize       daemonize process
  --dir DIR             set working directory
  --disable-history     disable message history
  --filter-own          enable filtering of own messages
  -h, --help            show this help message and exit
  --loglevel {debug,info,warn,error}
                        set logging level
  --port PORT           set AF_INET listen port
  --push-accounts       enable pushing accounts to client
  --sockfile SOCKFILE   set AF_UNIX socket file in DIR
  --version             show program's version number and exit
```


## Changes

* v0.8.3:
  * Update slixmpp to v1.8.5
* v0.8.2:
  * Update slixmpp to v1.8.4
* v0.8.1:
  * Update slixmpp to v1.8.3
* v0.8.0:
  * Update slixmpp to v1.8.0, which should fix python 3.10 issues
* v0.7.0:
  * Update nuqql-based to v0.3.0, switch to asyncio, require python
    version >= 3.7.
  * Add welcome and account adding help messages.
  * Disable filtering of own messages, rewrite sender of own messages to
    `<self>`
* v0.6.0:
  * Update nuqql-based to v0.2.0
* v0.5:
  * Use nuqql-based as dependency and adapt to nuqql-based changes
  * Add setup.py for installation and package distribution
  * Add python type annotations
  * Restructure code
* v0.4:
  * Add new commands:
    * `bye`: disconnect from the backend.
    * `quit`: quit the backend.
    * `help`: show list of commands and their description.
  * Add and use "chat msg" message format for group chat messages
  * Store accounts in .ini file `accounts.ini` in the backend's working
    directory. Note: existing accounts have to be re-added to the backend to
    be usable with the .ini file.
  * Add configuration file support: in addition to the command line arguments,
    configuration parameters can now be set in the .ini file `config.ini` in
    the backend's working directory.
  * Add `loglevel` configuration parameter to command line arguments and
    configuration file for setting the logging level to `debug`, `info`,
    `warn`, or `error`. Default: `warn`.
  * Make daemon python module optional
  * Fixes and improvements
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
