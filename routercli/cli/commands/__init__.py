"""Command registry. Importing this package registers every command."""

from routercli.cli.parser import Registry

registry = Registry()

from routercli.cli.commands import exec_cmds, global_cmds, interface_cmds, routing_cmds, dhcp_cmds, acl_nat_cmds  # noqa: E402,F401
