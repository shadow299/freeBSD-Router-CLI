"""CLI modes and their prompts."""

EXEC = "exec"                 # router>
PRIV = "priv"                 # router#
CONFIG = "config"             # router(config)#
IF = "config-if"              # router(config-if)# / (config-subif)#
OSPF = "config-router-ospf"   # router(config-router)#
RIP = "config-router-rip"
BGP = "config-router-bgp"
DHCP = "dhcp-config"          # router(dhcp-config)#
ACL_STD = "config-std-nacl"
ACL_EXT = "config-ext-nacl"

EXEC_MODES = (EXEC, PRIV)
SUB_MODES = (IF, OSPF, RIP, BGP, DHCP, ACL_STD, ACL_EXT)
CONFIG_MODES = (CONFIG,) + SUB_MODES

PROMPT_SUFFIX = {
    EXEC: ">", PRIV: "#", CONFIG: "(config)#", IF: "(config-if)#", OSPF: "(config-router)#",
    RIP: "(config-router)#", BGP: "(config-router)#", DHCP: "(dhcp-config)#",
    ACL_STD: "(config-std-nacl)#", ACL_EXT: "(config-ext-nacl)#",
}


def prompt(hostname: str, mode: str, context=None) -> str:
    if mode == IF and context and "." in str(context):
        return f"{hostname}(config-subif)#"
    return hostname + PROMPT_SUFFIX[mode]
