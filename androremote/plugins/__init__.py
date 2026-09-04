"""AndroRemote modular plugin framework."""

from androremote.plugins.base import Command, Plugin, PluginContext, command, hook
from androremote.plugins.manager import PluginManager

__all__ = ["Plugin", "PluginContext", "Command", "PluginManager", "command", "hook"]
