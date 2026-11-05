# freebsd-router-cli

A Cisco IOS-style management shell for a headless **FreeBSD 15.x** (also 14.x) router appliance built on
native **pf**, **if_vlan**, **dnsmasq** and (optionally) **FRR** for dynamic routing.

```
edge1>enable
Password:
edge1#configure terminal
edge1(config)#interface em1.10
edge1(config-subif)#ip address 192.168.10.1 255.255.255.0
edge1(config-subif)#ip nat inside
edge1(config-subif)#exit
edge1(config)#ip nat inside source list 1 interface em0 overload
edge1(config)#commit confirmed 5 comment guest vlan
Commit 7 applied. Automatic rollback in 5 minute(s) unless confirmed with 'commit'.
edge1(config)#commit
edge1(config)#end
edge1#write memory
[OK]
```

## Contents

| Path | Purpose |
|---|---|
| [routercli/cli/](routercli/cli/) | CLI engine: [app.py](routercli/cli/app.py) (session, modes, REPL, pager, pipes), [parser.py](routercli/cli/parser.py) (grammar compiler, abbreviation, `?` help, completion), [types.py](routercli/cli/types.py) (typed parameters), [modes.py](routercli/cli/modes.py) |
| [routercli/cli/commands/](routercli/cli/commands/) | Command modules: exec/show, global, interface, routing (static/OSPF/RIP/BGP), DHCP, ACL & NAT |
| [routercli/config/](routercli/config/) | [model.py](routercli/config/model.py) typed config schema + strict validation, [showrun.py](routercli/config/showrun.py) `show running-config` renderer, [store.py](routercli/config/store.py) client of the privileged backend |
| [routercli/backend/](routercli/backend/) | Translation engine: renderers for rc.conf / pf.conf / dnsmasq.conf / frr.conf / ntp.conf / resolv.conf, [apply.py](routercli/backend/apply.py) transactional commit, [helper.py](routercli/backend/helper.py) root entry point |
| [bin/](bin/) | `router-cli` (login shell) and `router-apply` (privileged backend) launchers |
| [setup/](setup/) | [install.sh](setup/install.sh), [setup_accounts.sh](setup/setup_accounts.sh), [harden_sshd.sh](setup/harden_sshd.sh) + [sshd_config.router-cli](setup/sshd_config.router-cli), [doas.conf](setup/doas.conf), [rc.d/routercli](setup/rc.d/routercli) |
| [docs/BUILDING_FREEBSD.md](docs/BUILDING_FREEBSD.md) | Building FreeBSD 15 from source (custom router kernel, trimmed world, image) |
| [docs/COMMANDS.md](docs/COMMANDS.md) | Full command reference (generated from the grammar) |
| [tests/](tests/) | End-to-end tests in sandbox mode |

## Architecture and security model

```
 ssh router@r1 ──► sshd ──ForceCommand──► /usr/local/bin/router-cli   (uid router, unprivileged)
                                              │  candidate config, parser, show commands
                                              │  JSON over stdin
                                              ▼
                          doas -n /usr/local/libexec/router-apply      (uid 0, single doas rule)
                                              │  1. re-parse + re-validate JSON (schema, regex whitelists)
                                              │  2. render rc.conf / pf.conf / dnsmasq.conf / frr.conf
                                              │  3. validate: sh -n, pfctl -nf, dnsmasq --test, vtysh -C
                                              │  4. backup → atomic write (fsync+rename) of changed files
                                              │  5. live apply: ifconfig/route/pfctl/service (diff only)
                                              │  6. on any failure: restore files + inverse live diff
                                              ▼
                     /var/db/router-cli  (root:router 0750)  running / startup / archive / pending
```

* **root** keeps a normal FreeBSD shell (`/bin/sh`) and can log in on the console and over SSH
  with its **password** or an SSH key (`PermitRootLogin yes`). `setup_accounts.sh` refuses to
  continue until root has a password. Restrict later with `ROOT_LOGIN=prohibit-password` (keys
  only) or `ROOT_LOGIN=no` (console only).
* **router** has `/usr/local/bin/router-cli` as its login shell *and* `ForceCommand` in sshd, so
  console, SSH, `su - router` all land in the CLI. `ssh router@r1 'show ip route'` is interpreted
  by the CLI (from `SSH_ORIGINAL_COMMAND`), never by a POSIX shell. sftp/scp, port/agent/X11
  forwarding and tunnels are denied. The account is not in `wheel`, its home is root-owned.
* The CLI never writes system files. Its only privileged path is one doas rule to
  `router-apply`, which trusts nothing from the CLI: unknown keys are rejected, every value that
  ends up in `rc.conf` is whitelisted by regex, and subprocesses run with argv lists and a fixed
  environment (no shell). Operational commands (`clear`, `reload`, ...) are a fixed whitelist.
* The python launchers run with `python3 -I` (isolated mode: `PYTHON*` env vars and user
  site-packages are ignored).
* Privileged EXEC (`enable`) is protected by the `enable secret`, stored as an scrypt hash
  (`enable secret 9 ...`), verified in constant time, 3 attempts per `enable`.
* Every commit is logged to syslog (`auth.notice`, tag `router-cli`) with the doas user.

## Installation (on the FreeBSD box, as root)

```sh
pkg install -y git                    # or copy this folder with scp
git clone <your repo> /root/freebsd-router-cli   # or: scp -r freebsd-router-cli root@box:
cd /root/freebsd-router-cli

sh setup/install.sh                   # python3 (auto: py312 on 15), prompt-toolkit, dnsmasq, doas, frr; installs files
ROUTER_PUBKEY=/root/router.pub ROOT_PUBKEY=/root/admin.pub \
  sh setup/setup_accounts.sh          # root shell, 'router' user, passwords, enable secret
sh setup/harden_sshd.sh               # sshd: ForceCommand for router, root key-only, AllowUsers
```

Keep your current root session open and test `ssh router@<ip>` and `ssh root@<ip>` (password)
from a second terminal.
`harden_sshd.sh` validates with `sshd -t` and `sshd -T -C user=router,...` before installing,
keeps a timestamped backup, and only reloads sshd (existing sessions survive).

Options: `NO_PKG=1` (packages pre-installed / offline), `NO_FRR=1` (no dynamic routing),
`PYVER=312` (default: auto-detect), `REALTEK=auto|yes|no`, `REALTEK_DRIVER=vendor|native`, `REALTEK_JUMBO=1`, `ROOT_LOGIN=yes|prohibit-password|no`
(default `yes`), `PASSWORD_AUTH=yes|no`, `ALLOW_USERS="router root admin"`,
`FORCE_INIT=1` (re-initialise the config store), `SET_ROOT_PW=1`, `SET_ROUTER_PW=1`.

> **Security:** password logins for root are enabled, so do not expose SSH on the WAN side. pf
> blocks all inbound traffic on `ip nat outside` interfaces by default; if you open port 22 there
> with an ACL, limit the source addresses (`permit tcp 198.51.100.0 0.0.0.255 any eq 22`).
>
> **Note:** `AllowUsers` defaults to `router root`. Add any other SSH account you need, or you
> will lock it out.

### Realtek NICs (RTL8168H + RTL8125B)

Both work after `install.sh`, which detects Realtek NICs with `pciconf` and sets up the Realtek
vendor driver:

* **RTL8168H** (1 GbE, `10ec:8168`) is supported by FreeBSD's built-in `re(4)`, so it works
  immediately, including in the FreeBSD installer. Use it as the uplink while installing.
* **RTL8125B** (2.5 GbE, `10ec:8125`) is **not** supported by the built-in `re(4)` in FreeBSD 14.x or 15.0/15.1.
  `install.sh` installs `net/realtek-re-kmod` and adds to `/boot/loader.conf`:

  ```
  if_re_load="YES"
  if_re_name="/boot/modules/if_re.ko"
  hw.re.max_rx_mbuf_sz="2048"
  ```

  **Reboot once.** The vendor driver then handles both cards as `re0` and `re1` (check with
  `show ip interface brief`; the order follows the PCI bus). With a self-built kernel, build the
  module with it via `PORTS_MODULES` (see [docs/BUILDING_FREEBSD.md](docs/BUILDING_FREEBSD.md)).

Example: 2.5G WAN (DHCP), 1G LAN with a guest VLAN:

```
interface re1
 description WAN (RTL8125B 2.5G)
 ip address dhcp
 ip nat outside
interface re0
 description LAN (RTL8168H)
 ip address 192.168.1.1 255.255.255.0
 ip nat inside
interface re0.10
 ip address 192.168.10.1 255.255.255.0
 ip nat inside
```

If an RTL8125 stalls under load (a known issue with some driver/firmware combinations), disable
checksum offload on it: `interface re1` → `no hardware-offload` → `commit`.

### Manual equivalent (what the scripts do)

```sh
pw groupadd router
echo /usr/local/bin/router-cli >> /etc/shells
pw useradd router -g router -s /usr/local/bin/router-cli -d /home/router -c "Router management CLI"
passwd router
passwd root                                   # root: password login on console + SSH
echo 'permit nopass router as root cmd /usr/local/libexec/router-apply' >> /usr/local/etc/doas.conf
install -d -o root -g router -m 0750 /var/db/router-cli
# /etc/ssh/sshd_config:  PermitRootLogin yes, PasswordAuthentication yes,
#                        Match User router / ForceCommand /usr/local/bin/router-cli ...
sshd -t && service sshd reload
```


## FreeBSD 15 compatibility

FreeBSD 15 is the primary target. The following was checked against the `releng/15.1` sources
and the FreeBSD:15:amd64 package repository:

| Area | Status on 15 |
|---|---|
| pf: the generated `nat`/`rdr`/`binat`, `scrub`, tables, `round-robin sticky-address` | supported (pf in 15 accepts both classic and `nat-to` syntax) |
| rc.conf: `vlans_<if>`, `ifconfig_<if>_<vid>`, `static_routes`, `gateway_enable`, `pf_enable`, `pflog_enable` | unchanged |
| `dhclient` (DHCP client on WAN), `ntpd`, `etcupdate` | still in base |
| Packages: `python312` + `py312-prompt-toolkit`, `dnsmasq`, `doas`, `frr10`, `realtek-re-kmod` | available; `install.sh` detects the Python flavour |
| RTL8168H | built-in `re(4)` or vendor driver |
| RTL8125B | vendor `realtek-re-kmod` (default), or native `rge(4)` on stable/15 / 15.2+ (`REALTEK_DRIVER=native`; the interface is then `rge0`) |
| pkgbase installs ("Packages (Tech Preview)") | supported; see BUILDING_FREEBSD.md §5b |

## Using the CLI

| Mode | Prompt | Enter with | Leave with |
|---|---|---|---|
| User EXEC | `r1>` | login | `exit` |
| Privileged EXEC | `r1#` | `enable` (enable secret) | `disable` |
| Global config | `r1(config)#` | `configure terminal` | `end`, Ctrl-Z, `exit` |
| Interface | `r1(config-if)#` / `r1(config-subif)#` | `interface em0` / `interface em0.10` | `exit` |
| Routing | `r1(config-router)#` | `router ospf 1` / `router rip` / `router bgp 65001` | `exit` |
| DHCP pool | `r1(dhcp-config)#` | `ip dhcp pool LAN` | `exit` |
| Named ACL | `r1(config-ext-nacl)#` | `ip access-list extended NAME` | `exit` |

* `?` anywhere for context help, **Tab** to complete (keywords, interface names from the live
  system, ACL/pool names), ↑/↓ history (persisted in `~/.router_cli_history`).
* Keywords can be abbreviated: `sh ip int br`, `conf t`, `int em0`, `no shut`.
* Pipes: `| include`, `| exclude`, `| begin`, `| section`, `| count`. `--More--` pager
  (`terminal length 0` disables).
* `do <exec command>` from any config mode; submode commands fall back to global config
  (typing `interface em2` inside `router ospf` works as on IOS).

### Configuration lifecycle

| Command | Effect |
|---|---|
| (config commands) | edit the **candidate** configuration only |
| `show configuration changes` | unified diff running → candidate |
| `commit [comment TEXT]` | validate, render, apply live and persist to `/etc` files; archived |
| `commit confirmed [MIN]` | as commit, automatic rollback after MIN minutes (default 10) unless `commit` is entered again; also rolled back if the box reboots meanwhile |
| `abort` | discard the candidate, return to EXEC |
| `write memory` / `copy running-config startup-config` | snapshot running → startup-config |
| `copy startup-config running-config` | load startup-config into the candidate and commit it |
| `rollback [ID]` / `show archive` / `show archive diff ID` | restore any of the last 30 commits |
| `write erase` | delete the saved startup-config |

`commit` already makes the change survive reboots (it writes `/etc/rc.conf`, `/etc/pf.conf`,
...), exactly like the boot-time configuration of a stock FreeBSD system. `startup-config` is the
"known good" snapshot you can return to with `copy startup-config running-config`.

### Translation to FreeBSD

| Cisco command | FreeBSD artefact |
|---|---|
| `hostname`, `ip domain-name` | `hostname=` in rc.conf + `hostname(1)` |
| `interface em0` / `ip address A M [secondary]` | `ifconfig_em0="inet A/len"`, `ifconfig_em0_aliasN` |
| `interface em0.10` + `encapsulation dot1Q 10` | `vlans_em0="10"`, `ifconfig_em0_10=...`; live `ifconfig em0.10 create vlan 10 vlandev em0` |
| `ip address dhcp` | `ifconfig_em0="DHCP"`, `dhclient` |
| `ipv6 address`, `mtu`, `description`, `shutdown` | `ifconfig_X_ipv6`, `mtu N`, `ifconfig_X_descr`, `down` |
| `no hardware-offload` | `-rxcsum -txcsum -rxcsum6 -txcsum6 -tso -lro` on the interface |
| `interface lo1` | `cloned_interfaces` |
| `ip route ...` / `ipv6 route ...` / `Null0` | `static_routes` / `ipv6_static_routes`, `defaultrouter`, `-blackhole` |
| `ip routing` / `ipv6 unicast-routing` | `gateway_enable` / `ipv6_gateway_enable` |
| `ip nat inside|outside` | pf `$inside` / `$outside` macros; outside gets a stateful default-deny inbound |
| `ip nat inside source list N interface X overload` | `nat on X inet from <acl permits> -> (X)` |
| `ip nat pool` + `... pool P` | `nat ... -> { a b c } round-robin sticky-address` |
| `ip nat inside source static L G` | `binat on <outside> from L to any -> G` |
| `ip nat inside source static tcp L lp interface X gp` | `rdr pass on X proto tcp to (X) port gp -> L port lp` |
| `access-list`, `ip access-list`, `ip access-group NAME in|out` | pf `quick` rules per interface + implicit deny |
| `ip dhcp pool`, `ip dhcp excluded-address`, `static-bind` | dnsmasq `dhcp-range` (exclusions subtracted), `dhcp-option`, `dhcp-host` |
| `ip dns server`, `ip name-server`, `ip host` | dnsmasq DNS forwarder, `server=`, `host-record=` |
| `ntp server` | `/etc/ntp.conf`, `ntpd_enable` |
| `router ospf` / `router rip` / `router bgp` | `/usr/local/etc/frr/frr.conf`, `frr_daemons=` |

Only a managed block at the end of `/etc/rc.conf` (between `# >>> router-cli managed block`
markers) is written; everything else in rc.conf is preserved, and the block overrides earlier
assignments. pf.conf, dnsmasq.conf and frr.conf are fully owned by router-cli.

Failure handling: every generated file is validated before installation (`pfctl -nf`,
`dnsmasq --test`, `vtysh -C`, `sh -n`). Live changes are applied as a diff (only the affected
interfaces/routes/services are touched; links do not flap). If any step fails, the previous
files are restored and the inverse diff is applied, and running-config stays unchanged.

## Development / testing without FreeBSD

Sandbox mode redirects all paths under a directory and logs (instead of running) mutating
commands, so the full stack runs on Linux/macOS:

```sh
python3 -m venv .venv && .venv/bin/pip install prompt_toolkit
export ROUTERCLI_SANDBOX=/tmp/rcli ROUTERCLI_IFACES="em0 em1 em2"
mkdir -p $ROUTERCLI_SANDBOX/etc && touch $ROUTERCLI_SANDBOX/etc/rc.conf
python3 -c 'import sys,json;sys.path.insert(0,".");from routercli.auth import hash_secret;print(json.dumps({"config":{"hostname":"r1","enable_secret":hash_secret("cisco123")}}))' \
  | python3 -I bin/router-apply init
.venv/bin/python -I bin/router-cli                  # interactive; enable secret: cisco123
python3 -m unittest discover -s tests -v             # test suite
python3 tools/gen_command_reference.py               # refresh docs/COMMANDS.md
```

Rendered files land in `$ROUTERCLI_SANDBOX/etc/...`; commands that would have run are in
`$ROUTERCLI_SANDBOX/var/db/router-cli/sandbox-commands.log`.

## Notes and limitations

* pf `nat` always port-translates; `overload` vs. non-overload is cosmetic.
* Undefined ACLs referenced by `ip access-group` permit everything (IOS behaviour), with a warning.
* `redistribute static` in OSPF/RIP/BGP maps to FRR `redistribute kernel` (static routes are
  installed by rc.d/route, not by FRR staticd).
* Interfaces you configure in the CLI override existing rc.conf lines for the same interface;
  interfaces not mentioned in the CLI config are left as they are.
* `dhclient` may rewrite `/etc/resolv.conf`; when `ip name-server` is configured router-cli writes
  resolv.conf at commit time.
* `realtek-re-kmod` from pkg comes from the kmods repository and is built for the installed
  minor release (e.g. 15.1). That works with a self-built kernel of the same `releng` branch,
  but rebuilding it with `PORTS_MODULES` is the safe choice.
* Verify on your FreeBSD release: FRR package name (`pkg search frr`), `vtysh -C` syntax checking.
