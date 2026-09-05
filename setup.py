#!/usr/bin/env python3
from setuptools import setup, find_packages

setup(
    name="androremote",
    version="1.0.0",
    description="Headless Android remote-management agent & C2 server",
    packages=find_packages(include=["androremote*"]),
    package_data={"androremote": ["web/dist/*", "web/dist/assets/*"]},
    python_requires=">=3.10",
    install_requires=[
        "rich>=13.0",
        "cryptography>=42.0",
    ],
    entry_points={
        "console_scripts": [
            "androremote=androremote.cli:main",
            "c2=androremote.c2:main",
        ],
    },
)
