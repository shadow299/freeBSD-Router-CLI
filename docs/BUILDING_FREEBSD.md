# Building FreeBSD 15 from source for the router appliance

This guide builds a FreeBSD **15.x** (RELENG) world and a router-tuned kernel from source,
installs it, then deploys router-cli. Everything also works for 14.x: use `releng/14.x` branch
names, and see the [FreeBSD 14 notes](#freebsd-14-notes) at the end. Building takes roughly 1-3 h on 4-8 cores (`buildworld` dominates);
you can run it **in parallel** with router-cli development, which runs in sandbox mode on any OS.

Official reference: FreeBSD Handbook, chapter *Updating and Upgrading FreeBSD → Updating
FreeBSD from Source* (<https://docs.freebsd.org/en/books/handbook/cutting-edge/#makeworld>) and
`build(7)`, `src.conf(5)`, `config(5)`, `etcupdate(8)`, `freebsd-base(7)`.

### What is different in FreeBSD 15 (and how this project handles it)

| Change in 15 | Effect on the appliance |
|---|---|
| Installer offers **"Distribution Sets"** or **"Packages (Tech Preview)"** (pkgbase) | Both work. Pick **Distribution Sets** for the classic `installworld` flow in §5, or follow §5b on a pkgbase system. Don't mix the two. |
| Default ports Python is **3.12** (`py312-prompt-toolkit`; there is no `py311-` package) | `setup/install.sh` detects the flavour automatically (`PYVER` overrides it). router-cli is tested on 3.11, 3.12 and 3.15. |
| New **`FreeBSD-ports-kmods`** pkg repository with kernel modules built for the exact minor release | `pkg install realtek-re-kmod` gets a module that matches the 15.x kernel |
| pf accepts both the classic `nat`/`rdr`/`binat` rules and OpenBSD-style `nat-to`/`rdr-to` | The generated `pf.conf` (classic syntax) loads unchanged |
| **`rge(4)`** RTL8125/8126/8127 driver merged into `stable/15` (2026-10-31; not in 15.0/15.1) | Optional native driver for the RTL8125B (see §3) |
| i386, armv6 and 32-bit powerpc retired | Build for amd64 (or arm64) |
| `dhclient`, `ntpd`, `pflog`, `etcupdate`, rc.conf `vlans_*` / `ifconfig_*_descr` / `static_routes` | Unchanged (checked against `releng/15.1`) |

---

## 0. Build host

* A FreeBSD 15.x machine or VM (official 15.x installer; choose **Distribution Sets** for the
  classic flow; `kernel-dbg`/`lib32`/`tests` are not required). At least 4 GB RAM, 30 GB disk free for
  `/usr/src` + `/usr/obj`.
* It can be the router itself, or a faster machine whose result you install over NFS or ship as
  an image (see §6).
* Cross-building on Linux/macOS is supported upstream via `tools/build/make.py` (see §7) but the
  install/image steps still need FreeBSD.

```sh
pkg install -y git
```

## 1. Get the source

Use a **release engineering** branch (security/errata fixes only). List them and pick the newest
15.x (15.1 at the time of writing):

```sh
git ls-remote --heads https://git.FreeBSD.org/src.git 'releng/15.*'
git clone --branch releng/15.1 --depth 1 https://git.FreeBSD.org/src.git /usr/src   # adjust 15.x
```

`stable/15` is the development branch for the next minor release (15.2); it already has the
native `rge(4)` RTL8125 driver. `main` is CURRENT (16) and should not be used for an appliance.
Prefer `releng/15.x` together with the vendor Realtek driver; use `stable/15` only if you
specifically want `rge(4)`. Later updates: `git -C /usr/src pull --ff-only`.

## 2. Build options: `/etc/src.conf` (what goes into world)

Trim components a headless router does not need. This shortens the build and reduces attack
surface. Copy, then adjust:

```make
# /etc/src.conf - router appliance
WITHOUT_LLVM_TARGET_ALL=yes     # build clang only for the host arch (big build-time saving)
WITHOUT_LIB32=yes               # no 32-bit compat libs (amd64)
WITHOUT_TESTS=yes
WITHOUT_DEBUG_FILES=yes
WITHOUT_GAMES=yes
WITHOUT_BLUETOOTH=yes
WITHOUT_LPR=yes
WITHOUT_EXAMPLES=yes
WITHOUT_HTML=yes
WITHOUT_SHAREDOCS=yes
WITHOUT_PPP=yes                 # keep if you need PPPoE via ppp(8); mpd5 from ports is an alternative
WITHOUT_WIRELESS=yes            # remove if the box is a Wi-Fi AP
WITHOUT_FINGER=yes
WITHOUT_TALK=yes
WITHOUT_RBOOTD=yes
# Keep (do NOT disable): PF, INET, INET6, OPENSSH, OPENSSL, CRYPT, NETGRAPH, PAM, ZFS (if root on ZFS)
```

Faster incremental rebuilds, `/etc/src-env.conf`:

```make
WITH_META_MODE=yes
```

and load filemon: `kldload filemon; sysrc kld_list+=filemon`.

Check the effect of your knobs with `make -C /usr/src showconfig | less`.

## 3. Router kernel: `/usr/src/sys/amd64/conf/ROUTER`

GENERIC already contains `vlan`, `VIMAGE`, `ROUTE_MPATH` and most NIC drivers (em, igb, ix,
vtnet, ...). pf is a module in GENERIC; compiling it in avoids a module load at boot and lets you
enable ALTQ queueing:

```
# /usr/src/sys/amd64/conf/ROUTER
include         GENERIC
ident           ROUTER

# --- Packet filter / NAT (pf) ---
device          pf
device          pflog
device          pfsync

# --- Optional: ALTQ traffic shaping for pf ---
options         ALTQ
options         ALTQ_CBQ
options         ALTQ_RED
options         ALTQ_RIO
options         ALTQ_HFSC
options         ALTQ_PRIQ
options         ALTQ_FAIRQ
options         ALTQ_CODEL

# --- Optional: first-hop redundancy / bridging / LACP ---
device          carp
device          if_bridge
device          lagg
```

(For arm64 put the file under `sys/arm64/conf/`. To slim further, add `nodevice <name>` lines
only for devices that actually appear in your release's GENERIC.)

Optional build-time speed-up, only build the modules you need, in `/etc/make.conf`:

```make
# MODULES_OVERRIDE= dummynet if_bridge if_lagg if_vlan pf pflog pfsync carp filemon
```

### Realtek NICs (RTL8168H + RTL8125B)

| Chip | PCI ID | built-in `re(4)` (14.x, 15.x) | built-in `rge(4)` | `net/realtek-re-kmod` (Realtek vendor driver) |
|---|---|---|---|---|
| RTL8168H / RTL8111H (1 GbE) | `10ec:8168` | yes | - | yes |
| RTL8125B (2.5 GbE) | `10ec:8125` | **no** (shows as `none` in `pciconf -l`) | `stable/15` only (module `if_rge.ko`, expected in 15.2); not in 15.0/15.1 | yes |

**Recommended (any 14.x/15.x): the vendor driver**, which handles both cards as `re0`/`re1`.
Keep `device re` (inherited from GENERIC) in the ROUTER kernel as a fallback. The vendor module
from ports probes with a higher priority, so when it is loaded it drives **both** cards
(`re0`, `re1`). Because you build your own kernel, build that module from ports **together with
the kernel** so it always matches it. Put this in `/etc/make.conf`:

```make
# rebuild/install out-of-tree kernel modules with every buildkernel/installkernel
PORTS_MODULES=net/realtek-re-kmod
```

That needs a ports tree at `/usr/ports`, created *before* `buildkernel`:

```sh
git clone --depth 1 https://git.FreeBSD.org/ports.git /usr/ports    # or a quarterly branch: --branch YYYYQn
```

and the loader settings (`setup/install.sh` adds them automatically when it detects a Realtek
NIC):

```sh
sysrc -f /boot/loader.conf if_re_load=YES if_re_name=/boot/modules/if_re.ko
sysrc -f /boot/loader.conf hw.re.max_rx_mbuf_sz=2048    # avoid RX hangs; omit if you need jumbo frames
```

**Alternative on `stable/15` (15.2+): native `rge(4)`.** Build the source from `stable/15`.
`if_rge.ko` is built with the modules; to compile it in, add `device rge` to the ROUTER kernel
config. Then install with `REALTEK_DRIVER=native sh setup/install.sh`, which sets
`if_rge_load="YES"` and removes the vendor driver lines. The interface names become
**`re0` (RTL8168H) and `rge0` (RTL8125B)**. `rge(4)` is new in FreeBSD and much less tested than
the vendor driver. Do not load both: the vendor driver probes with a higher priority and
would take over the RTL8125B anyway.

After reboot verify with `kldstat -m pci/if_re`, `dmesg | grep '^re'`, `ifconfig re1`
(media `2500Base-T` on the RTL8125B). If you see transmit stalls under load, use
`no hardware-offload` on that interface in router-cli (it adds `-rxcsum -txcsum -rxcsum6
-txcsum6 -tso -lro` to its rc.conf line).

**First boot / installer:** the stock FreeBSD installer only sees the RTL8168H (`re0`). Use it
for the network during installation and for `pkg install realtek-re-kmod`. The RTL8125B
appears after the module is configured and the box is rebooted. Without any network, fetch the
package on another FreeBSD machine of the same major.minor version (`pkg fetch -d -o /tmp/pkgs realtek-re-kmod`), copy it
on a USB stick and `pkg add` it.

## 4. Build

```sh
cd /usr/src
NCPU=$(sysctl -n hw.ncpu)
make -j$NCPU buildworld                   2>&1 | tee /var/tmp/buildworld.log
make -j$NCPU buildkernel KERNCONF=ROUTER  2>&1 | tee /var/tmp/buildkernel.log
```

(With `PORTS_MODULES` set, `buildkernel` also builds `realtek-re-kmod` against the new kernel
and `installkernel` installs it to `/boot/modules/if_re.ko`.)

If a build fails, rerun without `-j` (`make buildworld`) to get a readable error, and check the
`tail` of the log. After changing `src.conf`, do a clean build: `make cleanworld`.

## 5. Install (on the router itself)

Order matters (new kernel first, then userland, then config merge):

```sh
cd /usr/src
make installkernel KERNCONF=ROUTER
shutdown -r now             # boot the new kernel (single-user mode recommended:
                            # at the loader menu choose "Boot Single user")
# --- after reboot, as root ---
cd /usr/src
etcupdate -p                # pre-world merge (passwd/group)
make installworld
etcupdate -B                # merge /etc changes (resolve conflicts: etcupdate resolve)
make delete-old             # remove obsolete files (answer y, or use -DBATCH_DELETE_OLD_FILES)
shutdown -r now
# --- after reboot ---
cd /usr/src && make delete-old-libs
uname -a                    # ... ROUTER
```

### 5b. Systems installed with base packages (pkgbase)

If the box was installed with **"Packages (Tech Preview)"**, the base system is managed by pkg.
Do not use `installworld`/`installkernel`. Turn your build into a local package repository and
upgrade from it:

```sh
cd /usr/src
make -j$NCPU buildworld buildkernel KERNCONF=ROUTER
make update-packages KERNCONF=ROUTER          # -> /usr/obj/usr/src/repo/<ABI>/latest

mkdir -p /usr/local/etc/pkg/repos
cat > /usr/local/etc/pkg/repos/FreeBSD-base.conf <<'CONF'
FreeBSD-base: { enabled: no }
local-base: {
  url: "file:///usr/obj/usr/src/repo/${ABI}/latest",
  signature_type: "none",
  enabled: yes
}
CONF
pkg update -r local-base
pkg upgrade -r local-base                     # installs your ROUTER kernel + world
# pkg merges /etc itself (3-way); check for *.pkgnew files, don't run etcupdate
shutdown -r now
```

`PORTS_MODULES` is not packaged by `update-packages`. On pkgbase systems install the Realtek
driver with `pkg install realtek-re-kmod` (from the kmods repository) instead.

Make it the default kernel config for future builds in `/etc/make.conf`:

```make
KERNCONF=ROUTER
```

If the new kernel does not boot: at the loader prompt choose **"Select kernel → kernel.old"**
(or `boot kernel.old`), fix, rebuild.

## 6. Building elsewhere / producing an image

**NFS install** (build on a fast host, install on the router): export `/usr/src` and `/usr/obj`
read-only from the build host, mount both on the router at the same paths, and run §5 on the
router. Both systems must use the same `src.conf`/`make.conf`.

**Install into a staging directory** (e.g. for a jail, VM disk or custom image):

```sh
make installworld  DESTDIR=/stage/router
make installkernel DESTDIR=/stage/router KERNCONF=ROUTER
make distribution  DESTDIR=/stage/router     # /etc defaults
```

**Release media** (memstick / ISO / VM images with your kernel and src.conf):

```sh
cd /usr/src/release
make -C /usr/src/release clean
make -C /usr/src/release KERNCONF=ROUTER WITHOUT_DOCS=yes NOPORTS=yes NOSRC=yes memstick
# -> /usr/obj/usr/src/<arch>/release/memstick.img ; or 'vm-image WITH_VMIMAGES=yes VMFORMATS=raw'
```

For images, add the vendor driver too: `pkg -r /stage/router install -y realtek-re-kmod` (or
copy `/boot/modules/if_re.ko` built via `PORTS_MODULES`) and put the two `if_re_*` lines into
`/stage/router/boot/loader.conf`.

**NanoBSD** (read-only root, two firmware slices, atomic upgrades - the classic FreeBSD router
appliance layout): see `/usr/src/tools/tools/nanobsd/` and nanobsd(8). Put `KERNCONF=ROUTER`
and the `src.conf` knobs into the NanoBSD config (`NANO_KERNEL`, `CONF_WORLD`), and add the
packages from §8 with `cust_pkgng`. When using a read-only root, point router-cli's state dir
`/var/db/router-cli` and `/etc` to the persistent `/cfg` partition (`save_cfg`).

## 7. Cross-building from Linux / macOS (optional)

```sh
# needs clang, lld, llvm (same major version), libarchive, bmake is bootstrapped automatically
git clone --branch releng/15.1 --depth 1 https://git.FreeBSD.org/src.git ~/freebsd-src
cd ~/freebsd-src
MAKEOBJDIRPREFIX=$HOME/freebsd-obj tools/build/make.py \
    --cross-bindir=/usr/lib/llvm-18/bin TARGET=amd64 TARGET_ARCH=amd64 \
    -j$(nproc) buildworld buildkernel KERNCONF=GENERIC
```

(Use the path of your LLVM installation for `--cross-bindir`; a custom KERNCONF must be placed in
`sys/amd64/conf/` first.) Installing still happens on FreeBSD via NFS/DESTDIR as in §6.

## 8. Post-install: packages and router-cli

```sh
# networking sanity
sysrc gateway_enable=YES
sysrc sshd_enable=YES && service sshd start

# packages (official binary repo; switch to the 'latest' branch in
# /usr/local/etc/pkg/repos/FreeBSD.conf if you want newer FRR)
pkg bootstrap -y && pkg update

# deploy router-cli (copy the project from your workstation)
scp -r ~/Desktop/freebsd-router-cli root@<router>:/root/
ssh root@<router>
cd /root/freebsd-router-cli
sh setup/install.sh
sh setup/setup_accounts.sh
sh setup/harden_sshd.sh
```

Then `ssh router@<router>` → `enable` → `configure terminal`.

If you rebuild packages yourself instead (fully from source), use `poudriere` with a jail made
from your own world (`poudriere jail -c -j r15 -m src=/usr/src`) and build
`lang/python312 devel/py-prompt-toolkit dns/dnsmasq security/doas net/frr10 net/realtek-re-kmod`.

## 9. Keeping it updated

* Source security fixes: `git -C /usr/src pull`, then §4-§5 (only `buildkernel`/`installkernel`
  if the advisory is kernel-only).
* Packages: `pkg upgrade` (re-run `sh setup/install.sh NO_PKG=1` if the python minor version
  changes, so the launcher shebang points to the right interpreter).
* router-cli state lives in `/var/db/router-cli`; back it up together with `/etc/rc.conf`,
  `/etc/pf.conf`, `/usr/local/etc/dnsmasq.conf`, `/usr/local/etc/frr/`.

## Quick test lab (bhyve / QEMU / VirtualBox)

Hypervisors do not emulate the RTL8125, so test the Realtek driver on the real hardware (or pass
the NICs through with bhyve `ppt`/VFIO). Give the VM 2-3 NICs (e.g. `virtio-net` → `vtnet0..2`, or `e1000` → `em0..2`): one NAT/bridged
"WAN", one or two internal "LAN" networks with client VMs. 802.1Q subinterfaces work on both
virtio-net and e1000. Inside the CLI, `show ip interface brief` lists the real interface names
to use.

## FreeBSD 14 notes

* Branches: `releng/14.x` / `stable/14`. There is no `rge(4)`, so the RTL8125B needs
  `realtek-re-kmod`, which is the default.
* Older 14.x package sets use Python 3.11 (`py311-prompt-toolkit`); `install.sh` picks the right
  one. The pkg repository names are `FreeBSD` / `FreeBSD-kmods` instead of `FreeBSD-ports` /
  `FreeBSD-ports-kmods`.
* No pkgbase option in the installer; follow §5.
