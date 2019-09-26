# nuqql-based

nuqql-based is a dummy network daemon that implements the nuqql interface. It
can be used as a dummy backend for [nuqql](https://github.com/hwipl/nuqql),
e.g., for testing or as a basis for the implementation of other nuqql
backends.

Other backends using nuqql-based:
* [nuqql-slixmppd](https://github.com/hwipl/nuqql-slixmppd): a backend for the
  XMPP (Jabber) protocol
* [nuqql-matrixd](https://github.com/hwipl/nuqql-matrixd): a backend for the
  Matrix protocol

Dependencies:
* [daemon](https://pypi.org/project/python-daemon/) (optional): for daemonize
  support
