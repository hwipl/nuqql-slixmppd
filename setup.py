#!/usr/bin/env python3

"""
nuqql-slixmppd setup file
"""

from setuptools import setup

VERSION = "0.4"
DESCRIPTION = "XMPP client network daemon using slixmpp"
with open("README.md", 'r') as f:
    LONG_DESCRIPTION = f.read()
CLASSIFIERS = [
    "Programming Language :: Python :: 3",
    "License :: OSI Approved :: MIT License",
]

setup(
    name="nuqql-slixmppd",
    version=VERSION,
    description=DESCRIPTION,
    license="MIT",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    author="hwipl",
    author_email="nuqql-slixmppd@hwipl.net",
    url="https://github.com/hwipl/nuqql-slixmppd",
    packages=["nuqql_slixmppd"],
    entry_points={
        "console_scripts": ["nuqql-slixmppd = nuqql_slixmppd.slixmppd:main"]
    },
    classifiers=CLASSIFIERS,
    python_requires='>=3.6',
    install_requires=["slixmpp"],
)
