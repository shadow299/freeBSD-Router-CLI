# router-cli by example: a beginner's guide

This guide starts at "how do I log in" and ends with complete office configurations. Each topic
explains the idea in plain words, shows the commands to type, and tells you how to check the
result.

> Every example in this file is tested automatically (`tests/test_examples.py`), so the
> commands below are known to be accepted exactly as written.

## Contents

1. [Before you start](#1-before-you-start)
2. [Logging in and the CLI modes](#2-logging-in-and-the-cli-modes)
3. [Getting help: `?`, Tab, short commands, `do`, filters](#3-getting-help--tab-short-commands-do-filters)
4. [The golden rule: change, check, commit, save](#4-the-golden-rule-change-check-commit-save)
5. [Basic system settings](#5-basic-system-settings)
6. [Interfaces and IP addresses](#6-interfaces-and-ip-addresses)
7. [Recipe: a complete home router](#7-recipe-a-complete-home-router)
8. [DHCP server](#8-dhcp-server)
9. [DNS](#9-dns)
10. [VLANs](#10-vlans)
11. [NAT and port forwarding](#11-nat-and-port-forwarding)
12. [Firewall with access lists (ACLs)](#12-firewall-with-access-lists-acls)
13. [Static routing](#13-static-routing)
14. [Dynamic routing: OSPF](#14-dynamic-routing-ospf)
15. [Dynamic routing: RIP](#15-dynamic-routing-rip)
16. [Dynamic routing: BGP](#16-dynamic-routing-bgp)
17. [IPv6](#17-ipv6)
18. [Saving, history and rollback](#18-saving-history-and-rollback)
19. [Monitoring and troubleshooting](#19-monitoring-and-troubleshooting)
20. [Recipe: small office with VLANs](#20-recipe-small-office-with-vlans)
21. [Common mistakes and what the errors mean](#21-common-mistakes-and-what-the-errors-mean)
22. [Cheat sheet](#22-cheat-sheet)

---

## 1. Before you start

**The example box.** All examples use the two Realtek ports of this appliance:

| Port | Chip | Name in the CLI | Typical use |
|---|---|---|---|
| 1 GbE | RTL8168H | `re0` | **LAN** (your switch / Wi-Fi access point) |
| 2.5 GbE | RTL8125B | `re1` | **WAN** (cable to the ISP modem / ONT) |

Your names may differ. `show ip interface brief` lists the real ones, and with the native
`rge(4)` driver the 2.5G port is called `rge0`.

**Words you will meet**

| Word | Meaning |
|---|---|
| LAN | Your inside network (PCs, phones, printers) |
| WAN | The outside network (the internet / ISP) |
| IP address + mask | `192.168.1.1 255.255.255.0` = address `192.168.1.1` in network `192.168.1.0/24` (256 addresses) |
| Gateway / default route | Where packets go when the router has no better route, usually your ISP |
| DHCP | Hands out IP addresses to devices automatically |
| DNS | Turns names (`example.com`) into IP addresses |
| NAT | Lets many inside devices share one public IP address |
| VLAN | Splits one physical cable/switch into several separate networks |
| ACL | A list of permit/deny rules: a firewall |

**Masks you will use most**

| Mask | Prefix | Addresses | Wildcard (for ACL/OSPF) |
|---|---|---|---|
| `255.255.255.255` | /32 | 1 (a single host) | `0.0.0.0` |
| `255.255.255.252` | /30 | 4 (2 usable, point-to-point links) | `0.0.0.3` |
| `255.255.255.0` | /24 | 256 (254 usable) | `0.0.0.255` |
| `255.255.0.0` | /16 | 65536 | `0.0.255.255` |
| `255.0.0.0` | /8 | 16.7 million | `0.255.255.255` |
| `0.0.0.0` | /0 | everything | `255.255.255.255` |

A **wildcard mask** is the mask "upside down" (`255` - each number). ACLs and OSPF use it.

**How to read the examples.** Lines starting with `!` are comments, you can type or skip them.
Indentation is only for readability. Every block starts at the `router#` prompt (after
`enable`).

---

## 2. Logging in and the CLI modes

Connect from a PC on the LAN (or the console) as the `router` user:

```text
$ ssh router@192.168.1.1
Password: ********

router>                          <- User EXEC: look only
router> enable
Password: ********               <- the "enable secret"
router#                          <- Privileged EXEC: show everything, save, reload
```

The CLI is a ladder of **modes**. The prompt always tells you where you are:

| Prompt | Mode | How to get there | What you do there |
|---|---|---|---|
| `router>` | User EXEC | log in | basic `show`, `ping` |
| `router#` | Privileged EXEC | `enable` | all `show`, `write memory`, `rollback`, `reload` |
| `router(config)#` | Global configuration | `configure terminal` | settings for the whole router |
| `router(config-if)#` | Interface | `interface re0` | IP address, NAT side, ACL of one port |
| `router(config-subif)#` | VLAN interface | `interface re0.10` | the same for one VLAN |
| `router(dhcp-config)#` | DHCP pool | `ip dhcp pool LAN` | DHCP settings of one network |
| `router(config-router)#` | Routing protocol | `router ospf 1` / `router rip` / `router bgp 65001` | dynamic routing |
| `router(config-std-nacl)#` / `router(config-ext-nacl)#` | ACL | `ip access-list ...` | firewall rules |

Moving around:

```cli
! go down into configuration mode, then into an interface
configure terminal
interface re0
! "exit" goes up one level
exit
! "end" (or Ctrl+Z) goes straight back to router#
end
show privilege
```

* `disable` goes back from `router#` to `router>`.
* `exit` at `router#` or `router>` logs you out.
* As the `router` user you **never** get a FreeBSD shell. For that, log in as `root`.

---

## 3. Getting help: `?`, Tab, short commands, `do`, filters

You do not have to remember commands. These keys help you (interactive only):

| Key | What it does | Example |
|---|---|---|
| `?` | Lists what you can type next, with a description | `show ?`, `ip ?`, `interface re0` then `ip address ?` |
| `?` right after letters | Lists commands starting with those letters | `sh?` |
| Tab | Completes the word (interfaces too: `re0`, `re0.10`) | `conf`+Tab, `int r`+Tab |
| Up / Down | Command history (kept between logins) | |
| Ctrl+Z | Same as `end` | |
| Ctrl+C | Cancel the current line | |

**Short commands.** Any word can be shortened as long as it is unique:

```cli
sh ver
sh ip int br
conf t
int re0
end
```

`sh ip int br` = `show ip interface brief`, `conf t` = `configure terminal`.

**`do`**: run a `router#` command without leaving configuration mode:

```cli
configure terminal
do show running-config
end
```

**Output filters** (`|`), handy for long output:

```cli
show running-config | include hostname
show running-config | begin interface
show running-config | section dns
show running-config | exclude !
show running-config | count interface
```

| Filter | Shows |
|---|---|
| `include WORD` | only lines containing WORD |
| `exclude WORD` | all lines except those containing WORD |
| `begin WORD` | everything from the first line with WORD |
| `section WORD` | blocks whose first line contains WORD (with their indented lines) |
| `count WORD` | how many lines contain WORD |

`terminal length 0` turns off the `--More--` paging for this session.

---

## 4. The golden rule: change, check, commit, save

This is the most important section. router-cli works like Cisco IOS-XR / Juniper:

1. **Change**: commands in configuration mode only edit a *candidate* (draft) configuration.
   Nothing on the router changes yet.
2. **Check**: `show configuration changes` shows exactly what you changed.
3. **Commit**: `commit` checks everything, writes the FreeBSD files (`rc.conf`, `pf.conf`,
   `dnsmasq.conf`, `frr.conf`) and applies them live. If anything fails, *nothing* is changed.
4. **Save**: `write memory` makes the current configuration the startup configuration.

```cli
configure terminal
hostname edge1
! what will change?
show configuration changes
! apply it
commit
end
! keep it as the startup configuration
write memory
```

Notice the prompt changes to `edge1` immediately, but the system is only changed at `commit`.

**Changed your mind?** `abort` throws the draft away and goes back to `router#`:

```cli
configure terminal
hostname wrong-name
abort
show running-config | include hostname
```

**Working remotely? Use `commit confirmed`.** If a change locks you out (wrong IP, too strict
firewall), the router undoes it automatically after the given minutes unless you confirm with
a second `commit`:

```cli
configure terminal
banner motd Authorized access only
commit confirmed 5 comment testing new banner
! still connected and everything works? confirm it:
commit
end
```

If you leave config mode with unsaved changes, the CLI asks
`Uncommitted changes found, commit them before exiting(yes/no/cancel)?`.

---

## 5. Basic system settings

```cli
configure terminal
! name of the router (shown in the prompt and in logs)
hostname edge1
! the enable password (stored as a strong hash, never in clear text)
enable secret MyStr0ngEnable
! message shown to everyone at login
banner motd Authorized access only. All activity is logged.
! domain name for this network
ip domain-name home.lan
! DNS servers the router itself uses (and forwards client queries to)
ip name-server 1.1.1.1 8.8.8.8
! keep the clock correct
ntp server pool.ntp.org
ntp server time.google.com
commit
end
write memory
```

Check:

```cli
show running-config | begin hostname
show hosts
```

Remove a setting by putting `no` in front of it:

```cli
configure terminal
no ntp server time.google.com
no banner motd
commit
end
```

* Passwords, descriptions and banners may contain letters, digits, spaces and basic
  punctuation (`. , : ; / @ # ( ) + = ! ? * % & - _`), but **no quotes or `$`**.
* Changing the *login* passwords (`router`, `root`) is done as root with `passwd router` /
  `passwd root`. `enable secret` only changes the `enable` password.

---

## 6. Interfaces and IP addresses

**Static address on the LAN port:**

```cli
configure terminal
interface re0
 description LAN switch
 ip address 192.168.1.1 255.255.255.0
 no shutdown
commit
end
show running-config interface re0
```

**Get the WAN address from the ISP by DHCP** (most home connections):

```cli
configure terminal
interface re1
 description WAN to ISP
 ip address dhcp
commit
end
```

With `ip address dhcp` the ISP also gives you the default route and DNS servers.

**Static WAN address** (business lines: the ISP gives you IP, mask and gateway):

```cli
configure terminal
interface re1
 ip address 203.0.113.2 255.255.255.252
 exit
! the gateway goes into a default route (see section 13)
ip route 0.0.0.0 0.0.0.0 203.0.113.1
commit
end
```

**A second network on the same port** (`secondary`):

```cli
configure terminal
interface re0
 ip address 192.168.50.1 255.255.255.0 secondary
commit
end
```

**Other interface settings:**

```cli
configure terminal
interface re0
 ! larger packets (only if every device on the LAN supports it)
 mtu 9000
 ! back to the default
 no mtu
 ! turn the port off / on
 shutdown
 no shutdown
 ! Realtek 2.5G card dropping connections under load? turn offloading off
 no hardware-offload
 hardware-offload
commit
end
```

**Remove an address / reset an interface:**

```cli
configure terminal
interface re0
 no ip address 192.168.50.1 255.255.255.0 secondary
 no description
commit
end
```

`no ip address` removes all IPv4 addresses. `no interface re0` removes all CLI settings of
that port (an older line in `/etc/rc.conf`, e.g. from the installer, applies again after a
reboot).

Just *entering* an interface (`interface re1`, look around, `exit`) changes nothing. Only the
settings you type count.

Check your work:

```text
router# show ip interface brief
router# show interfaces re0
router# show running-config interface re0
```

---

## 7. Recipe: a complete home router

Goal: internet from the ISP on `re1`, home network `192.168.1.0/24` on `re0`, addresses for
all devices by DHCP, the router as DNS server, and NAT so everyone can share the one public
address. Unsolicited traffic from the internet is blocked automatically on the
`ip nat outside` interface.

```text
   Internet
      |
   [ISP modem/ONT]
      |  (DHCP from ISP)
     re1  WAN, ip nat outside
  [ this router ]
     re0  LAN 192.168.1.1/24, ip nat inside
      |
   [switch / Wi-Fi AP] -- PCs, phones, TV (192.168.1.50 - .254 by DHCP)
```

Type all of this (copy and paste works too):

```cli
configure terminal
hostname home-router
ip domain-name home.lan
!
! ---- WAN ----
interface re1
 description WAN to ISP
 ip address dhcp
 ip nat outside
 exit
!
! ---- LAN ----
interface re0
 description Home LAN
 ip address 192.168.1.1 255.255.255.0
 ip nat inside
 exit
!
! ---- NAT: everyone in 192.168.1.0/24 shares the WAN address ----
access-list 1 permit 192.168.1.0 0.0.0.255
ip nat inside source list 1 interface re1 overload
!
! ---- DHCP: keep .1 - .49 for fixed devices, hand out .50 - .254 ----
ip dhcp excluded-address 192.168.1.1 192.168.1.49
ip dhcp pool HOME
 network 192.168.1.0 255.255.255.0
 default-router 192.168.1.1
 dns-server 192.168.1.1
 domain-name home.lan
 lease 1
 exit
!
! ---- DNS: the router answers DNS for the LAN ----
ip dns server
!
commit confirmed 5 comment home router
commit
end
write memory
```

What each part does:

| Lines | Meaning |
|---|---|
| `ip address dhcp` + `ip nat outside` | Get the WAN address from the ISP; this is the "outside" (firewalled) side |
| `ip nat inside` | This is a LAN side whose traffic gets translated |
| `access-list 1 permit ...` | *Which* addresses are allowed to be translated |
| `ip nat inside source list 1 interface re1 overload` | Translate them to re1's address (`overload` = many devices share one IP) |
| `ip dhcp excluded-address` | Addresses DHCP must never hand out |
| `ip dhcp pool HOME` | The DHCP settings for this network; `lease 1` = 1 day |

Now check:

```cli
show running-config
show ip dhcp pool HOME
```

```text
router# show ip interface brief        <- re1 should have an address from the ISP
router# show ip route                  <- there should be a "default" route via re1
router# ping 1.1.1.1                   <- internet works
router# ping example.com               <- DNS works
router# show ip dhcp binding           <- devices that got an address
router# show ip nat translations       <- active NAT sessions
```

On a PC, set the network to "automatic (DHCP)", it gets `192.168.1.50` or higher, gateway and
DNS `192.168.1.1`.

---

## 8. DHCP server

A **pool** is one network that gets DHCP. You need a pool for every LAN / VLAN.

**Basic pool:**

```cli
configure terminal
interface re0
 ip address 192.168.1.1 255.255.255.0
 exit
ip dhcp pool LAN
 network 192.168.1.0 255.255.255.0
 default-router 192.168.1.1
 dns-server 192.168.1.1
commit
end
```

The router's own address (`192.168.1.1`) is never handed out. The pool is served on the
interface that has an address in the pool's network (here `re0`).

**Keep addresses free for fixed devices** (servers, printers, access points):

```cli
configure terminal
! single address
ip dhcp excluded-address 192.168.1.2
! a range
ip dhcp excluded-address 192.168.1.10 192.168.1.49
commit
end
```

**Always give the same address to one device** (a "static lease" / reservation). You need the
device's MAC address. Both `aa:bb:cc:dd:ee:ff` and Cisco style `aabb.ccdd.eeff` are accepted:

```cli
configure terminal
ip dhcp pool LAN
 static-bind aa:bb:cc:11:22:33 192.168.1.20 nas
 static-bind 0011.2233.4455 192.168.1.21 printer
commit
end
```

The optional name at the end (`nas`) also becomes a DNS name: `nas.home.lan` if you set a
domain.

**More pool options:**

```cli
configure terminal
ip dhcp pool LAN
 ! domain suffix for clients
 domain-name home.lan
 ! two DNS servers
 dns-server 192.168.1.1 1.1.1.1
 ! lease time: days [hours [minutes]]  - here 0 days 8 hours
 lease 0 8
commit
end
```

`lease infinite` never expires. `no lease` returns to the default (1 day).

**Remove a reservation / a pool:**

```cli
configure terminal
ip dhcp pool LAN
 no static-bind 0011.2233.4455
 exit
no ip dhcp excluded-address 192.168.1.2
commit
end
```

`no ip dhcp pool LAN` deletes the whole pool.

Check:

```cli
show ip dhcp pool
show running-config | section dhcp
```

```text
router# show ip dhcp binding        <- which device has which address right now
```

---

## 9. DNS

By default the router is a DNS *forwarder* (`ip dns server`): clients ask the router, the
router asks the upstream servers and caches the answers.

**Choose the upstream DNS servers** (otherwise the ones from the ISP via DHCP are used):

```cli
configure terminal
ip name-server 1.1.1.1 9.9.9.9
commit
end
```

**Local names**, so you can type `nas` instead of `192.168.1.20`:

```cli
configure terminal
ip domain-name home.lan
ip host nas 192.168.1.20
ip host printer 192.168.1.21
ip host router 192.168.1.1
commit
end
show hosts
```

PCs can now use `nas`, `nas.home.lan`, `printer`, ...

**Remove entries / turn the DNS forwarder off:**

```cli
configure terminal
no ip host printer
no ip name-server
commit
end
```

`no ip dns server` turns the forwarder off. Then give clients other DNS servers in each DHCP
pool (`dns-server 1.1.1.1`).

---

## 10. VLANs

A VLAN lets one cable carry several separate networks. The switch adds a *tag* (a number
1-4094) to every packet so the router knows which network it belongs to.

To make a VLAN, configure an interface named **`<port>.<vlan-number>`**. For example
`re0.10` = VLAN 10 on port `re0`. The router creates it on `commit`.

```text
             re0 (trunk)
  [router] ================ [managed switch] ---- port 1-8:  VLAN 1 (untagged) home
                                             ---- port 9-12: VLAN 10 guests
                                             ---- port 13-16: VLAN 20 IoT/cameras
```

**Step 1: the VLAN interfaces**

```cli
configure terminal
! the port itself = untagged (native) network
interface re0
 description Home LAN untagged
 ip address 192.168.1.1 255.255.255.0
 ip nat inside
 exit
! VLAN 10
interface re0.10
 description Guests
 ip address 192.168.10.1 255.255.255.0
 ip nat inside
 exit
! VLAN 20
interface re0.20
 description IoT
 ip address 192.168.20.1 255.255.255.0
 ip nat inside
 exit
commit
end
show vlans
```

Every VLAN needs **its own network** (`192.168.10.0/24`, `192.168.20.0/24`, ...). The address
on the VLAN interface is the gateway for that VLAN.

**Step 2: internet (NAT) and DHCP for each VLAN**

```cli
configure terminal
interface re1
 ip address dhcp
 ip nat outside
 exit
! one ACL covering all 192.168.x.x networks
access-list 1 permit 192.168.0.0 0.0.255.255
ip nat inside source list 1 interface re1 overload
!
ip dhcp pool GUESTS
 network 192.168.10.0 255.255.255.0
 default-router 192.168.10.1
 dns-server 192.168.10.1
 exit
ip dhcp pool IOT
 network 192.168.20.0 255.255.255.0
 default-router 192.168.20.1
 dns-server 192.168.20.1
 exit
commit
end
```

**Step 3 (recommended): keep guests away from your home network**

Without this, devices in different VLANs can reach each other through the router. This
firewall rule on the guest VLAN allows DHCP and DNS to the router and the internet, but
nothing in `192.168.x.x`:

```cli
configure terminal
ip access-list extended GUEST-IN
 remark allow DHCP and DNS to the router
 permit udp any any eq 67
 permit udp any host 192.168.10.1 eq 53
 permit tcp any host 192.168.10.1 eq 53
 remark block all private networks
 deny ip any 192.168.0.0 0.0.255.255
 deny ip any 10.0.0.0 0.255.255.255
 deny ip any 172.16.0.0 0.15.255.255
 remark everything else (= internet) is fine
 permit ip any any
 exit
interface re0.10
 ip access-group GUEST-IN in
commit
end
```

**Remove a VLAN:**

```cli
configure terminal
no ip dhcp pool IOT
no interface re0.20
commit
end
```

**On the switch** (any managed switch):

* The port to the router (`re0`) is a **trunk**: VLANs 10 and 20 **tagged**, the home network
  **untagged** (native VLAN 1).
* Guest ports: **access** VLAN 10 (untagged). IoT ports: access VLAN 20.
* Wi-Fi: give each SSID its VLAN ID in the access point settings and make its switch port a
  trunk too.

The VLAN number must match the name: inside `interface re0.10`, `encapsulation dot1Q 10` is
accepted (Cisco habit) but not needed.

---

## 11. NAT and port forwarding

**NAT overload (PAT)**: all inside devices share the WAN address. This is what you want 99% of
the time. You need: inside interfaces (`ip nat inside`), the outside interface
(`ip nat outside`), an ACL saying *who* gets translated, and the NAT rule.

```cli
configure terminal
interface re1
 ip address dhcp
 ip nat outside
 exit
interface re0
 ip address 192.168.1.1 255.255.255.0
 ip nat inside
 exit
access-list 1 permit 192.168.1.0 0.0.0.255
ip nat inside source list 1 interface re1 overload
commit
end
```

**Port forwarding**: let the internet reach a server inside. Example: web server
`192.168.1.20`. Internet port 80 → server port 80, and internet port 8443 → server port 443:

```cli
configure terminal
ip nat inside source static tcp 192.168.1.20 80 interface re1 80
ip nat inside source static tcp 192.168.1.20 443 interface re1 8443
! a game server on UDP
ip nat inside source static udp 192.168.1.30 27015 interface re1 27015
commit
end
```

Format: `ip nat inside source static tcp|udp <inside-ip> <inside-port> interface <wan> <outside-port>`.
The firewall opening for the forwarded port is created automatically. Give the server a fixed
address (static-bind, section 8) so the forward does not break.

Some well-known port names also work instead of numbers: `www` (80), `https` (443), `ssh` (22),
`domain` (53), `smtp` (25), ... Press `?` at the port position to see them.

**Exclude some traffic from NAT** with `deny` lines (for example traffic to a VPN/partner network
that must keep the real source address):

```cli
configure terminal
no access-list 1
access-list 1 deny 192.168.1.128 0.0.0.127
access-list 1 permit 192.168.1.0 0.0.0.255
commit
end
```

Here `192.168.1.128 - .255` are *not* translated. ACL lines are checked top-down, the first
match wins.

**Static 1:1 NAT**: an inside server gets its *own* public address (your ISP must have given
you several public IPs, here `203.0.113.8/29`):

<!-- fresh -->

```cli
configure terminal
interface re1
 ip address 203.0.113.10 255.255.255.248
 ip nat outside
 exit
interface re0
 ip address 192.168.1.1 255.255.255.0
 ip nat inside
 exit
ip route 0.0.0.0 0.0.0.0 203.0.113.9
! server 192.168.1.20 is 203.0.113.11 on the internet, in both directions
ip nat inside source static 192.168.1.20 203.0.113.11
commit
end
```

**NAT pool**: translate to a range of public addresses instead of the interface address:

```cli
configure terminal
ip nat pool PUBLIC 203.0.113.12 203.0.113.14 netmask 255.255.255.248
access-list 2 permit 192.168.1.0 0.0.0.255
ip nat inside source list 2 pool PUBLIC overload
commit
end
```

Check:

```cli
show running-config | include nat
```

```text
router# show ip nat translations    <- live sessions
router# show ip nat statistics
router# show pf nat                 <- the generated FreeBSD pf NAT rules
router# clear ip nat translation *  <- drop all sessions (they re-establish)
```

---

## 12. Firewall with access lists (ACLs)

**What is already protected without any ACL?** Every `ip nat outside` interface blocks traffic
that was not started from inside (port forwards and static NAT excepted). Ping and
traceroute replies are allowed. Inside networks can talk to each other and to the internet.

ACLs add your own rules. An ACL is a list of lines; for every packet the lines are checked
**top-down, the first match wins**, and at the end there is an invisible **`deny ip any any`**.
The ACL does nothing until you attach it to an interface with `ip access-group NAME in|out`:

* `in` = packets *coming into the router* on that interface (most common, use this).
* `out` = packets *leaving the router* on that interface.

**Address formats inside ACLs**

| You write | Matches |
|---|---|
| `any` | every address |
| `host 192.168.1.20` | exactly one address |
| `192.168.1.0 0.0.0.255` | the whole 192.168.1.0/24 network (wildcard mask!) |
| `192.168.1.0 0.0.0.127` | 192.168.1.0 - 192.168.1.127 |

**Two kinds of ACL**

* **Standard** (`access-list 1-99`, or `ip access-list standard NAME`) matches only the
  *source* address. Used for NAT and simple "this network yes/no".
* **Extended** (`access-list 100-199`, or `ip access-list extended NAME`) matches protocol,
  source, destination and ports. Used for real firewall rules.

Named ACLs are easier to read and edit, so prefer them.

**Example 1: block one device from the internet** (a kid's tablet, `192.168.1.77`):

```cli
configure terminal
interface re0
 ip address 192.168.1.1 255.255.255.0
 exit
ip access-list standard LAN-IN
 remark no internet for the tablet
 deny host 192.168.1.77
 permit any
 exit
interface re0
 ip access-group LAN-IN in
commit
end
```

Without the `permit any` line, *everything* would be blocked (invisible deny at the end).

**Example 2: allow only web browsing and DNS from a network**:

```cli
configure terminal
interface re1
 ip address dhcp
 ip nat outside
 exit
ip access-list extended WEB-ONLY
 remark DHCP and DNS
 permit udp any any eq 67
 permit udp any any eq domain
 permit tcp any any eq domain
 remark web
 permit tcp any any eq www
 permit tcp any any eq 443
 remark ping is useful for troubleshooting
 permit icmp any any
 deny ip any any log
 exit
interface re0
 ip access-group WEB-ONLY in
commit
end
```

`log` sends matching packets to the pf log (`tcpdump -n -e -ttt -i pflog0` as root).

**Example 3: only the admin PC may log in to the router (SSH, port 22)**:

```cli
configure terminal
ip access-list extended LAN-IN2
 permit tcp host 192.168.1.10 host 192.168.1.1 eq ssh
 deny tcp any host 192.168.1.1 eq ssh
 permit ip any any
 exit
interface re0
 ip access-group LAN-IN2 in
commit
end
```

> Be careful when filtering the interface you are connected through: use
> `commit confirmed 5`, then `commit` once you know you can still log in.

**Port ranges and other matches** (extended ACLs):

```cli
configure terminal
ip access-list extended EXAMPLES
 permit tcp any any range 5000 5100
 permit udp any any gt 1023
 deny tcp any any neq 25
 permit tcp any any established
 permit gre any any
 permit esp any any
commit
end
```

| Keyword | Meaning |
|---|---|
| `eq 80` / `neq 80` | port is / is not 80 |
| `lt 1024` / `gt 1023` | port lower / greater than |
| `range 5000 5100` | port between |
| `established` | only replies to connections that were already started |
| `log` | log every match |

A port written *after the source* is the source port; after the destination it is the
destination port: `permit tcp any eq 53 any` vs `permit tcp any any eq 53`.

**Editing a named ACL with sequence numbers.** Every line has a number (10, 20, 30, ...).
Insert between lines, or delete one line:

```cli
configure terminal
ip access-list extended EXAMPLES
 ! insert before line 20
 15 permit icmp any any
 ! delete line 30
 no 30
 exit
commit
end
show access-lists EXAMPLES
```

**Numbered ACLs** (classic Cisco style) work too, one line per command:

```cli
configure terminal
access-list 110 remark allow web to the NAS
access-list 110 permit tcp any host 192.168.1.20 eq 443
access-list 110 deny ip any host 192.168.1.20
access-list 110 permit ip any any
commit
end
```

To change a numbered ACL, delete it completely (`no access-list 110`) and type it again.

**Remove an ACL from an interface / delete it:**

```cli
configure terminal
interface re0
 no ip access-group in
 exit
no ip access-list extended LAN-IN2
commit
end
```

Check:

```cli
show access-lists
```

```text
router# show pf rules         <- the generated FreeBSD pf rules with hit counters
router# clear counters        <- reset the counters
```

---

## 13. Static routing

The router automatically knows the networks on its own interfaces. For anything else it
needs a **route**: "to reach network X, send packets to next-hop Y".

**Default route** ("everything else goes to the ISP"). Not needed with `ip address dhcp`,
the ISP's DHCP provides it.

```cli
configure terminal
interface re1
 ip address 203.0.113.2 255.255.255.252
 exit
ip route 0.0.0.0 0.0.0.0 203.0.113.1
commit
end
```

**Route to another inside network** behind a second router:

```text
 [this router] re0 192.168.1.1 ---- 192.168.1.254 [lab router] ---- 10.20.0.0/16
```

```cli
configure terminal
interface re0
 ip address 192.168.1.1 255.255.255.0
 exit
ip route 10.20.0.0 255.255.0.0 192.168.1.254
commit
end
```

**Route out of an interface** (point-to-point links and VLANs without a next-hop):

```cli
configure terminal
ip route 10.99.0.0 255.255.255.0 re0
commit
end
```

**Blackhole (`Null0`)**: silently drop traffic to a network, e.g. unused private ranges
that should never go to the ISP:

```cli
configure terminal
ip route 172.16.0.0 255.240.0.0 Null0
commit
end
```

**Remove a route:**

```cli
configure terminal
no ip route 10.99.0.0 255.255.255.0
commit
end
show running-config | include ip route
```

```text
router# show ip route       <- the routing table
router# traceroute 10.20.0.1
```

Routes are made permanent in `/etc/rc.conf` (`static_routes`), so they survive a reboot.

---

## 14. Dynamic routing: OSPF

With several routers, typing static routes everywhere gets painful. With **OSPF** routers
tell each other which networks they have. All routers in one area learn all routes, and if
a link fails they find another path within seconds.

(Dynamic routing uses FRR. `setup/install.sh` installs it unless you used `NO_FRR=1`.)

Lab: two routers connected by a cable, each with its own LAN.

```text
 LAN 192.168.1.0/24          link 10.0.12.0/30            LAN 192.168.2.0/24
  ---- re1 [ R1 ] re0 10.0.12.1 -------- 10.0.12.2 re0 [ R2 ] re1 ----
```

**R1:**

```cli
configure terminal
hostname R1
interface re0
 description link to R2
 ip address 10.0.12.1 255.255.255.252
 exit
interface re1
 description LAN 1
 ip address 192.168.1.1 255.255.255.0
 exit
router ospf 1
 router-id 1.1.1.1
 ! "network <addr> <wildcard> area <n>" = run OSPF on interfaces in this range
 network 10.0.12.0 0.0.0.3 area 0
 network 192.168.1.0 0.0.0.255 area 0
 ! never talk OSPF towards the LAN (no other routers there), but still announce it
 passive-interface re1
 exit
commit
end
```

<!-- fresh -->

**R2** is the mirror image:

```cli
configure terminal
hostname R2
interface re0
 description link to R1
 ip address 10.0.12.2 255.255.255.252
 exit
interface re1
 description LAN 2
 ip address 192.168.2.1 255.255.255.0
 exit
router ospf 1
 router-id 2.2.2.2
 network 10.0.12.0 0.0.0.3 area 0
 network 192.168.2.0 0.0.0.255 area 0
 passive-interface re1
 exit
commit
end
```

**Useful extras** (on the router that has the internet connection):

```cli
configure terminal
router ospf 1
 ! tell the other routers "send internet traffic to me"
 default-information originate
 ! also announce static routes and directly connected networks
 redistribute static subnets
 redistribute connected subnets
 ! do not send OSPF on any interface by default ...
 passive-interface default
 ! ... except on the link to R2
 no passive-interface re0
commit
end
```

Check (on a real box):

```text
R1# show ip ospf neighbor     <- R2 should be listed, state "Full"
R1# show ip route             <- 192.168.2.0/24 learned via 10.0.12.2
R1# show ip ospf interface
R1# show ip protocols
R1# clear ip ospf process     <- restart OSPF if needed
```

Rules of thumb: both ends of a link must be in the **same area** and the **same subnet**;
every router needs a unique `router-id`; area `0` is the backbone, so for a small network
put everything in area 0.

---

## 15. Dynamic routing: RIP

RIP is the simplest routing protocol (good for labs and very small networks; it allows at most
15 hops). Same lab as OSPF, R1 side:

```cli
configure terminal
interface re0
 ip address 10.0.12.1 255.255.255.252
 exit
interface re1
 ip address 192.168.1.1 255.255.255.0
 exit
router rip
 version 2
 ! RIP "network" is classful: give the network address without a mask
 network 10.0.0.0
 network 192.168.1.0
 passive-interface re1
commit
end
```

Extras:

```cli
configure terminal
router rip
 ! announce a default route to the neighbours
 default-information originate
 ! announce static routes with metric 2
 redistribute static metric 2
commit
end
```

```text
router# show ip rip
router# show ip rip status
router# show ip route
```

Remove: `no router rip`.

---

## 16. Dynamic routing: BGP

BGP is the routing protocol of the internet. You need it when you connect to two ISPs with
your own address space, or to a partner/data-centre network. Each side has an **AS number**
(private range: 64512-65534).

```text
  [this router, AS 65001] re1 10.0.12.1 ------ 10.0.12.2 [ISP / partner, AS 65002]
           re0 192.168.1.0/24 (announced)
```

```cli
configure terminal
interface re1
 ip address 10.0.12.1 255.255.255.252
 exit
interface re0
 ip address 192.168.1.1 255.255.255.0
 exit
router bgp 65001
 bgp router-id 10.0.12.1
 neighbor 10.0.12.2 remote-as 65002
 neighbor 10.0.12.2 description Partner network
 ! announce our LAN (must exist in the routing table: here it is connected)
 network 192.168.1.0 mask 255.255.255.0
commit
end
```

Temporarily disable / remove a neighbour, announce static routes:

```cli
configure terminal
router bgp 65001
 neighbor 10.0.12.2 shutdown
 no neighbor 10.0.12.2 shutdown
 redistribute static
commit
end
```

```text
router# show ip bgp summary   <- neighbour state; a number in the last column = established
router# show ip bgp           <- learned and announced routes
router# clear ip bgp *        <- reset the sessions
```

Remove a neighbour completely: `no neighbor 10.0.12.2`. Remove BGP: `no router bgp 65001`.

---

## 17. IPv6

```cli
configure terminal
! let the router forward IPv6
ipv6 unicast-routing
interface re0
 ipv6 address 2001:db8:1::1/64
 exit
interface re1
 ipv6 address 2001:db8:ffff::2/64
 exit
! IPv6 default route to the ISP
ipv6 route ::/0 2001:db8:ffff::1
! other IPv6 routes
ipv6 route 2001:db8:99::/48 2001:db8:1::fe
commit
end
```

```text
router# show ipv6 route
```

Note: ACLs, NAT and the DHCP server are IPv4 only. IPv6 is routed, and neighbour discovery
is always allowed.

---

## 18. Saving, history and rollback

There are three kinds of configuration:

| Name | What it is | Command to see it |
|---|---|---|
| candidate | your draft in config mode | `show candidate-config`, `show configuration changes` |
| running | what is active now (last `commit`) | `show running-config` |
| startup | what was saved with `write memory` | `show startup-config` |

Every `commit` is stored in an **archive**, so you can always go back:

```cli
configure terminal
hostname first
commit comment rename to first
hostname second
commit comment rename to second
end
! list of all commits
show archive
! what did commit 2 look like / how is it different from now?
show archive config 2 | include hostname
show archive diff 2
! undo the last commit
rollback
show running-config | include hostname
! go to a specific archive entry
rollback 2
```

Save and restore:

```cli
! save running -> startup (same as "copy running-config startup-config")
write memory
! load the saved startup configuration again (throws away unsaved commits)
copy startup-config running-config
! re-apply everything to the system (if something was changed outside the CLI)
commit force
```

`write erase` deletes the startup configuration. The running configuration stays active.

The CLI asks for confirmation for `rollback`, `copy ...`, `write erase` and `reload`.

---

## 19. Monitoring and troubleshooting

| Question | Command |
|---|---|
| What is this box, uptime, version? | `show version` |
| Are my ports up, which addresses? | `show ip interface brief`, `show interfaces re1` |
| Which VLANs exist? | `show vlans` |
| Where do packets go? | `show ip route`, `show ipv6 route` |
| Who is on my LAN? | `show arp` |
| Which devices got DHCP addresses? | `show ip dhcp binding` |
| Is NAT working? | `show ip nat translations`, `show ip nat statistics` |
| What does the firewall do? | `show access-lists`, `show pf rules`, `show pf states`, `show pf info` |
| Routing protocols | `show ip protocols`, `show ip ospf neighbor`, `show ip bgp summary`, `show ip rip` |
| Time sync | `show clock`, `show ntp associations` |
| System messages | `show logging` |
| Who is logged in | `show users` |
| Can I reach it? | `ping 8.8.8.8`, `ping example.com repeat 10 size 1400`, `traceroute 8.8.8.8` |

**Step-by-step when "the internet does not work":**

1. `show ip interface brief`: does `re1` have an address and is it `up`? If not: cable, ISP
   modem, `ip address dhcp` on `re1`?
2. `show ip route`: is there a `default` route? If not and the WAN is static:
   `ip route 0.0.0.0 0.0.0.0 <ISP gateway>`.
3. `ping 1.1.1.1` works but `ping example.com` does not: DNS. Check `show hosts` and
   `ip name-server`.
4. The router can ping but PCs cannot reach the internet: check NAT. `re0` needs `ip nat inside`,
   `re1` needs `ip nat outside`, and the NAT ACL must include the PC's network
   (`show running-config | include nat`, `show access-lists`).
5. PCs get no address: `show ip dhcp pool`. The pool network must match the address on
   the LAN interface. Then check `show ip dhcp binding`.
6. Still stuck: `show logging`, and as root `tail /var/log/messages`, `pfctl -sr`,
   `service dnsmasq status`.

**Using `ping` with a source** (test whether a LAN network is routed correctly):

```text
router# ping 8.8.8.8 source 192.168.1.1
```

---

## 20. Recipe: small office with VLANs

A complete, realistic configuration you can adapt:

* WAN: static public IP from the ISP on the 2.5G port `re1`.
* `re0` trunk to a managed switch: staff (untagged), VLAN 10 guests, VLAN 20 cameras,
  VLAN 30 servers.
* DHCP everywhere, a reserved address for the printer.
* The web server is reachable from the internet (HTTPS).
* Guests: internet only. Cameras: no internet and cannot start connections to other
  networks (staff can still open the camera web pages).
* Only the IT PC may SSH to the router.

```text
                       Internet
                          |
                203.0.113.2/30 (re1, gw .1)
                    [ office router ]
                          | re0 trunk
                   [ managed switch ]
     |               |                 |                 |
  staff (untagged)  VLAN 10 guests    VLAN 20 cameras   VLAN 30 servers
  192.168.1.0/24    192.168.10.0/24   192.168.20.0/24   192.168.30.0/24
```

```cli
configure terminal
hostname office-gw
enable secret Office-Enable-2026
banner motd Office gateway. Authorized staff only.
ip domain-name office.lan
ip name-server 1.1.1.1 9.9.9.9
ntp server pool.ntp.org
!
! ===== interfaces =====
interface re1
 description WAN static
 ip address 203.0.113.2 255.255.255.252
 ip nat outside
 exit
interface re0
 description Staff LAN untagged
 ip address 192.168.1.1 255.255.255.0
 ip nat inside
 exit
interface re0.10
 description Guests
 ip address 192.168.10.1 255.255.255.0
 ip nat inside
 exit
interface re0.20
 description Cameras
 ip address 192.168.20.1 255.255.255.0
 exit
interface re0.30
 description Servers
 ip address 192.168.30.1 255.255.255.0
 ip nat inside
 exit
ip route 0.0.0.0 0.0.0.0 203.0.113.1
!
! ===== NAT (cameras are not in the list: no internet for them) =====
ip access-list standard NAT-SRC
 permit 192.168.1.0 0.0.0.255
 permit 192.168.10.0 0.0.0.255
 permit 192.168.30.0 0.0.0.255
 exit
ip nat inside source list NAT-SRC interface re1 overload
! HTTPS to the web server
ip nat inside source static tcp 192.168.30.10 443 interface re1 443
!
! ===== DHCP =====
ip dhcp excluded-address 192.168.1.1 192.168.1.19
ip dhcp excluded-address 192.168.30.1 192.168.30.99
ip dhcp pool STAFF
 network 192.168.1.0 255.255.255.0
 default-router 192.168.1.1
 dns-server 192.168.1.1
 domain-name office.lan
 static-bind 00:11:22:33:44:55 192.168.1.5 printer
 exit
ip dhcp pool GUESTS
 network 192.168.10.0 255.255.255.0
 default-router 192.168.10.1
 dns-server 192.168.10.1
 lease 0 4
 exit
ip dhcp pool CAMERAS
 network 192.168.20.0 255.255.255.0
 default-router 192.168.20.1
 dns-server 192.168.20.1
 lease infinite
 exit
ip dhcp pool SERVERS
 network 192.168.30.0 255.255.255.0
 default-router 192.168.30.1
 dns-server 192.168.30.1
 exit
ip host www 192.168.30.10
!
! ===== firewall =====
ip access-list extended STAFF-IN
 remark only the IT PC may manage the router
 permit tcp host 192.168.1.10 any eq ssh
 deny tcp any host 192.168.1.1 eq ssh
 permit ip any any
 exit
ip access-list extended GUEST-IN
 permit udp any any eq 67
 permit udp any host 192.168.10.1 eq domain
 permit tcp any host 192.168.10.1 eq domain
 deny ip any 192.168.0.0 0.0.255.255
 deny ip any 10.0.0.0 0.255.255.255
 deny ip any 172.16.0.0 0.15.255.255
 permit ip any any
 exit
ip access-list extended CAMERAS-IN
 remark cameras may only answer staff and use DHCP/DNS/NTP on the router
 permit udp any any eq 67
 permit udp any host 192.168.20.1 eq domain
 permit udp any host 192.168.20.1 eq 123
 permit tcp any 192.168.1.0 0.0.0.255 established
 deny ip any any log
 exit
interface re0
 ip access-group STAFF-IN in
 exit
interface re0.10
 ip access-group GUEST-IN in
 exit
interface re0.20
 ip access-group CAMERAS-IN in
 exit
!
commit confirmed 10 comment office baseline
commit
end
write memory
```

Review the result:

```cli
show running-config
show vlans
show access-lists
show ip dhcp pool
```

**What the switch needs:** the port to `re0` as a trunk (VLAN 1 untagged, VLANs 10, 20, 30
tagged); staff ports access VLAN 1, guest Wi-Fi SSID VLAN 10, camera ports VLAN 20, server
ports VLAN 30.

---

## 21. Common mistakes and what the errors mean

**`% Invalid input detected at '^' marker.`**: a typo or a value that is not allowed. The `^`
shows where. Here the mask is not a valid mask:

```cli-error
configure terminal
interface re0
 ip address 192.168.1.1 255.255.0.255
```

**`% Incomplete command.`**: something is missing. Press `?` to see what can follow:

```cli-error
configure terminal
interface re0
 ip address 192.168.1.1
```

**`% Ambiguous command`**: the shortened word fits more than one command. Type more letters.

**Overlapping networks**: two interfaces cannot be in the same network:

```cli-error
configure terminal
interface re0
 ip address 192.168.1.1 255.255.255.0
 exit
interface re1
 ip address 192.168.1.2 255.255.255.0
```

**VLAN number does not match the name**: `re0.10` is always VLAN 10:

```cli-error
configure terminal
interface re0.10
 encapsulation dot1Q 20
```

**Quotes in descriptions**: not allowed (they would be unsafe in system files):

```cli-error
configure terminal
interface re0
 description John's laptop
```

Write `description Johns laptop` instead.

**Forgot to commit**: leaving config mode without `commit` asks you, and in a script the
changes are thrown away:

```cli-error
configure terminal
hostname forgotten
end
```

**A DHCP pool for a network no interface has**: committed, but with a warning, since no
device would ever get an address from it:

```cli-error
configure terminal
ip dhcp pool LOST
 network 10.77.0.0 255.255.255.0
 exit
commit
```

**Other typical problems**

| Symptom | Cause / fix |
|---|---|
| PCs have an address but no internet | `ip nat inside` / `ip nat outside` missing, or the PC's network is not in the NAT ACL |
| Port forward does not work | Test from *outside* (a phone on mobile data), not from the LAN; the server's own firewall; the server's gateway must be the router |
| Locked out after an ACL change | Wait for `commit confirmed` to roll back; otherwise log in on the console as `root` and run `router-cli`, then `rollback` |
| Changes gone after reboot | You never committed: `commit` writes the system files. `write memory` additionally saves the startup copy |
| `% Warning: ... does not exist on this system` | Interface name typo (`show ip interface brief`), or the Realtek driver is not loaded (see README) |
| 2.5G port drops under load | `interface re1` → `no hardware-offload` → `commit` |

---

## 22. Cheat sheet

```text
enable                                   go to router#
configure terminal  (conf t)             go to config mode
end / Ctrl+Z                             back to router#
?  /  Tab                                help / complete
do <command>                             run a router# command in config mode
no <command>                             remove a setting

show configuration changes               what will commit change?
commit                                   apply
commit confirmed 5                       apply, auto-undo in 5 min unless "commit" again
abort                                    discard the draft
write memory                             save as startup config
rollback [N]                             undo the last commit / go to archive N
show archive                             commit history

hostname NAME
enable secret PASSWORD
ip name-server 1.1.1.1 8.8.8.8
ntp server pool.ntp.org

interface re0
 description TEXT
 ip address 192.168.1.1 255.255.255.0
 ip address dhcp
 ip nat inside | ip nat outside
 ip access-group ACL in
 shutdown | no shutdown

interface re0.10                         VLAN 10 on re0

ip route 0.0.0.0 0.0.0.0 GATEWAY         default route
ip route NET MASK NEXTHOP                static route

access-list 1 permit 192.168.0.0 0.0.255.255
ip nat inside source list 1 interface re1 overload          internet sharing
ip nat inside source static tcp INSIDE-IP PORT interface re1 PORT   port forward

ip dhcp excluded-address FIRST LAST
ip dhcp pool NAME
 network 192.168.1.0 255.255.255.0
 default-router 192.168.1.1
 dns-server 192.168.1.1
 static-bind MAC IP [NAME]

ip access-list extended NAME
 permit|deny PROTO SRC [PORT] DST [PORT]
router ospf 1 / router rip / router bgp ASN

show running-config | include WORD
show ip interface brief / show ip route / show vlans
show ip dhcp binding / show ip nat translations / show access-lists
ping HOST / traceroute HOST
```

The full list of every command is in [COMMANDS.md](COMMANDS.md).
