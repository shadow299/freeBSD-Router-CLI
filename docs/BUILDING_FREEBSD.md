# Building the router installer (USB stick / ISO) - step by step

This guide is written for someone who has **never built FreeBSD before**. Follow the steps in
order and copy-paste the commands. Most of the time is spent waiting.

**What you get at the end:** one file, for example
`freebsd-router-15.2-PRERELEASE-amd64-20261105.img`. You write it to a USB stick, boot a PC
from the stick, answer a few questions, and the PC becomes a router. You can give the same
file to other people, and every installation works the same way.

**What is inside the image (and nothing else):**

| Part | What it is |
|---|---|
| FreeBSD 15 (branch `stable/15`) | The operating system, built from source, with unneeded parts removed (no compilers, games, docs, tests or 32-bit libraries; see [`image/src.conf`](../image/src.conf)) |
| `pf`, VLANs, DHCP client, PPPoE, SSH | Already part of FreeBSD |
| python, prompt_toolkit, dnsmasq, doas, FRR | The only extra packages. They are stored **on the stick**, so installing needs **no internet** |
| router-cli | This project: the Cisco-style CLI |

**Why `stable/15`?** It is the branch that becomes FreeBSD 15.2 and contains the new `rge`
driver. Both of your Realtek network cards work out of the box, without extra drivers:

| Your card | Speed | Name in FreeBSD |
|---|---|---|
| RTL8168H | 1 Gbit/s | `re0` |
| RTL8125B | 2.5 Gbit/s | `rge0` |

---

## Contents

* [Words used in this guide](#words-used-in-this-guide)
* [Part 0 - What you need](#part-0---what-you-need)
* [Part 1 - Prepare the build machine (only once)](#part-1---prepare-the-build-machine-only-once)
* [Part 2 - Build the installer image](#part-2---build-the-installer-image)
* [Part 3 - Write the image to a USB stick](#part-3---write-the-image-to-a-usb-stick)
* [Part 4 - Install on the router](#part-4---install-on-the-router)
* [Part 5 - First login](#part-5---first-login)
* [Part 6 - Giving the image to others](#part-6---giving-the-image-to-others)
* [Part 7 - Updates](#part-7---updates)
* [When something goes wrong](#when-something-goes-wrong)
* [Appendix A - What the build script does](#appendix-a---what-the-build-script-does)
* [Appendix B - Customising the image](#appendix-b---customising-the-image)
* [Appendix C - Other ways to install router-cli](#appendix-c---other-ways-to-install-router-cli)
* [Appendix D - RISC-V and FreeBSD 14](#appendix-d---risc-v-and-freebsd-14)

---

## Words used in this guide

| Word | Meaning |
|---|---|
| **build machine** | The computer (or virtual machine) where you create the image. It is *not* the router. |
| **router** | The box that will run the finished system (the one with the two Realtek cards). |
| **source code** | FreeBSD as text files. "Building" turns it into programs. |
| **world** / **kernel** | The two halves of FreeBSD: the kernel is the core, world is all the programs. |
| **image** (`.img`) | One file that contains a whole bootable USB stick. |
| **ISO** (`.iso`) | The same as an image, but for virtual machines and CDs/DVDs. |
| **root** | The administrator account on FreeBSD. A prompt ending in `#` means you are root. |
| **amd64** / **x64** | Normal Intel/AMD 64-bit PCs. |
| **arm64** | 64-bit ARM machines with UEFI firmware. |

In the boxes below, type the lines that **do not** start with `#`. A `#` line is only a
comment. When this guide shows example output, the box says so.

---

## Part 0 - What you need

| Item | Minimum | Recommended |
|---|---|---|
| Build machine (real PC or virtual machine) | 2 CPU cores, 4 GB RAM, 60 GB disk | 8 cores, 16 GB RAM, 100 GB disk |
| Internet on the build machine | needed to download the source (about 1 GB) and packages (about 150 MB) | |
| USB stick | 4 GB, **everything on it will be erased** | 8 GB or more |
| Router hardware | 64-bit PC (UEFI or BIOS) or arm64 with UEFI; 2 network ports | |
| Time | 2-4 hours, almost all of it waiting | |

The build machine must run **FreeBSD 15** (15.0 or 15.1 RELEASE is fine). It does not have to
be a spare PC: a **virtual machine** in VirtualBox, VMware, Hyper-V or QEMU on your normal
computer is perfect.

---

## Part 1 - Prepare the build machine (only once)

### Step 1.1 - Install FreeBSD 15 on the build machine

1. Download the installer from <https://www.freebsd.org/where/>: choose the newest
   **15.x-RELEASE**, **amd64**, file `...-amd64-disc1.iso`.
2. Create a virtual machine. VirtualBox example: *New* → Type **BSD**, Version
   **FreeBSD (64-bit)**, memory **8192 MB**, 4 CPUs, disk **100 GB**, then attach the ISO.
   Under *Network*, keep "NAT".
3. Start the VM and install FreeBSD with these answers:

| Installer screen | Answer |
|---|---|
| Welcome | **Install** |
| Keymap | your keyboard (or just continue) |
| Hostname | `builder` |
| Installation type | **Distribution Sets** |
| Distribution Select | nothing extra needed; press Enter |
| Partitioning | **Auto (ZFS)** → *Install* → select the disk with Space → *OK* → *YES* |
| Root password | choose one and remember it |
| Network | select the network card → IPv4 **Yes** → DHCP **Yes** → IPv6 No → DNS: Enter |
| Time zone | yours |
| System Configuration | keep **sshd** checked |
| Hardening | press Enter |
| Add users | **Yes**: username `builder`, and at "Invite builder into other groups?" type **`wheel`**. Other questions: press Enter. Set a password. |
| Final Configuration | **Exit**, then **No**, then **Reboot** (remove the ISO) |

### Step 1.2 - Log in as root

At the `login:` prompt, type `root` and your root password. The prompt ends with `#`.

If you prefer working from your normal computer's terminal (copy-paste is easier), find the
VM's address with `ifconfig | grep 'inet '`. In VirtualBox with NAT you first need a port
forward for SSH (Settings → Network → Advanced → Port Forwarding: host port 2222 → guest
port 22). Then:

```sh
# on your normal computer:
ssh -p 2222 builder@127.0.0.1       # or: ssh builder@<VM address>
# then become root on the build machine:
su -
```

(FreeBSD does not allow `root` to log in over SSH by default. That is why you log in as
`builder` and then use `su -`.)

### Step 1.3 - Install the two tools the build needs

```sh
pkg install -y git-lite tmux
```

Answer `y` if it asks to bootstrap pkg. `git-lite` downloads the FreeBSD source code.
`tmux` keeps the build running if your SSH connection drops. These tools stay on the build
machine only; they are **not** put into the router image.

### Step 1.4 - Copy this project to the build machine

From your normal computer, where the project folder is on the Desktop:

```sh
# on your normal computer (Linux/macOS terminal, or Windows PowerShell):
scp -r -P 2222 ~/Desktop/freebsd-router-cli builder@127.0.0.1:
# without VirtualBox port forwarding:   scp -r ~/Desktop/freebsd-router-cli builder@<VM address>:
```

Then on the build machine, as root:

```sh
cp -R /home/builder/freebsd-router-cli /root/
cd /root/freebsd-router-cli
ls
```

Expected output:

```text
README.md  bin  docs  image  routercli  setup  tests  tools
```

---

## Part 2 - Build the installer image

### Step 2.1 - Start tmux (recommended when you work over SSH)

```sh
tmux
```

If the connection drops, log in again, run `su -`, then `tmux attach` and you are back. You
can skip tmux if you type directly on the build machine's own screen.

### Step 2.2 - Run the build script

For normal PCs (x64 / amd64):

```sh
cd /root/freebsd-router-cli
sh image/build-image.sh
```

For ARM 64-bit machines:

```sh
sh image/build-image.sh -a arm64
```

(Your build machine stays a normal PC. The script "cross-builds" the ARM version, which
takes about twice as long.)

The script prints 8 steps. Expected output (shortened):

```text
[1/8] Checking the build machine
      FreeBSD 15.1-RELEASE, 8 CPU jobs, 16 GB RAM, 85 GB free
      building: stable/15 for amd64 (amd64/amd64), kernel GENERIC

[2/8] Getting the FreeBSD source code (stable/15) into /usr/src
      downloading (about 1 GB, 5-20 minutes)
      ...
[3/8] Building the FreeBSD base system (buildworld)
      this is the long part: about 1-3 hours on a 4-8 core PC (more for arm64)
      log: /usr/obj/router-image/logs/amd64-buildworld.log   (watch it live: tail -f ...)
```

While it seems to do nothing, it is working. To watch, open a second tmux window (press
`Ctrl+b`, then `c`) and run the `tail -f ...` command it shows. Press `Ctrl+c` to stop
*watching*; this does not stop the build. Switch back to the first window with `Ctrl+b`, then
`0`.

| Step | What happens | Typical time (8 cores) |
|---|---|---|
| 1 | checks FreeBSD version, disk space, RAM, git | seconds |
| 2 | downloads the `stable/15` source to `/usr/src` | 5-20 min |
| 3 | builds FreeBSD's programs (buildworld) | 1-2 h |
| 4 | builds the kernel | 10-20 min |
| 5 | packs FreeBSD and the installer | 10-20 min |
| 6 | downloads python, dnsmasq, doas, FRR packages | 1-5 min |
| 7 | adds router-cli and the packages to the installer | seconds |
| 8 | creates the `.img` and `.iso` files | 2-5 min |

### Step 2.3 - The result

At the end you see something like:

```text
Finished. Your installer images:
   1.4G  /usr/obj/router-image/images/freebsd-router-15.2-PRERELEASE-amd64-20261105.img
   1.4G  /usr/obj/router-image/images/freebsd-router-15.2-PRERELEASE-amd64-20261105.iso
```

| File | Use it for |
|---|---|
| `...img` | USB sticks (Part 3) |
| `...iso` | virtual machines (to test) and CD/DVD |
| `CHECKSUMS.SHA256` | lets people check that the download is not damaged |

**Running it again is cheap.** If a later step fails (for example the package download
because the internet was down), just run the same command again. Steps 3 and 4 are skipped
when the source has not changed, so you lose minutes, not hours.

Useful options (`sh image/build-image.sh -h` shows all of them):

| Option | Meaning |
|---|---|
| `-a arm64` | build for ARM 64-bit instead of PC |
| `-n` | leave out FRR (no OSPF/RIP/BGP), saves about 30 MB |
| `-u` | do not download source updates; build exactly what is in `/usr/src` |
| `-f` | force building world and kernel again |
| `-j 4` | use 4 parallel jobs (use fewer if the build machine runs out of memory) |

---

## Part 3 - Write the image to a USB stick

**Warning:** this erases the whole USB stick. If you pick the wrong disk, you erase that
disk instead. Read the device name twice.

### Step 3.1 - Copy the image to your normal computer (if you need to)

```sh
# on your normal computer:
scp -P 2222 'builder@127.0.0.1:/usr/obj/router-image/images/*' .
```

`builder` cannot read `/usr/obj/...` by default, so first run this on the build machine as
root: `cp /usr/obj/router-image/images/* /home/builder/ && chown builder /home/builder/*.img /home/builder/*.iso`.
Then copy from `builder@...:` without the path.

### Step 3.2 - Write it

**Windows:** install [Rufus](https://rufus.ie) → select the USB stick → *SELECT* the `.img`
file → *START*. If Rufus asks, choose **"Write in DD Image mode"**.
[balenaEtcher](https://etcher.balena.io) also works: *Flash from file* → *Select target* →
*Flash*.

**macOS:** use balenaEtcher (same as above).

**Linux:**

```sh
lsblk                        # find the stick by its size, e.g. sdb (NOT your system disk)
sudo dd if=freebsd-router-15.2-PRERELEASE-amd64-20261105.img of=/dev/sdb bs=1M conv=fsync status=progress
```

**FreeBSD** (for example the build machine with the stick passed through):

```sh
geom disk list | grep -E 'Geom name|descr|Mediasize'   # find the stick, e.g. da0
dd if=/usr/obj/router-image/images/freebsd-router-15.2-PRERELEASE-amd64-20261105.img of=/dev/da0 bs=1m conv=sync status=progress
```

### Step 3.3 (optional) - Test in a virtual machine first

Create a new VM (2 GB RAM, 10 GB disk, **two network cards**) and boot the `.iso`. Then
follow Part 4. The network cards are called `em0`/`em1` or `vtnet0`/`vtnet1` there instead of
`re0`/`rge0`.

---

## Part 4 - Install on the router

**Everything on the router's disk is erased.**

### Step 4.1 - Boot from the USB stick

1. Plug in the stick, a screen and a keyboard, and power on.
2. Press the boot-menu key right away. It is often **F12**, **F11**, **F8** or **Esc**; the
   first screen usually shows which one. Choose the USB stick (often listed as "UEFI: <stick
   name>").
3. If nothing boots, enter the firmware setup (**Del** or **F2**) and **disable Secure Boot**.
   FreeBSD's boot loader is not Secure Boot signed.
4. The FreeBSD boot menu appears and starts automatically after 10 seconds.

### Step 4.2 - Answer the installer screens

Use the **arrow keys**, **Tab** to jump to the buttons, **Space** to tick a box, and
**Enter** to confirm.

| # | Screen | What to do |
|---|---|---|
| 1 | Welcome | **Install** |
| 2 | Keymap Selection | choose your keyboard, or *Continue with default keymap* |
| 3 | Set Hostname | a name, e.g. `router1` (letters, digits, `-`) |
| 4 | Select Installation Type | **Distribution Sets** (important; "Packages" downloads the normal FreeBSD instead of this image's) |
| 5 | Partitioning | **Auto (ZFS)** if the router has 4 GB RAM or more, otherwise **Auto (UFS)** |
| 6 | ZFS: *Install* → select disk with **Space** → *OK* → *YES*. UFS: *Entire Disk* → *GPT* → *Finish* → *Commit* | |
| 7 | Archive Extraction | wait about 1 minute |
| 8 | Root password | type the **root** password twice. root gets a full FreeBSD shell |
| 9 | Network Configuration | you may choose **Cancel**: the router setup in step 11 configures the network |
| 10 | Time Zone, Date, Time, System Hardening | choose your time zone; press Enter on the rest |
| 11 | **Installing the router software** | see below |
| 12 | Final Configuration | **Exit** |
| 13 | Manual Configuration | **No** |
| 14 | Complete | **Reboot**, and **pull out the USB stick** while the screen goes dark |

**Step 11 in detail** (text screens, about 1 minute):

```text
==> Installing packages from the installer (no network needed)
...
==> Creating the 'router' account
  You now choose:
    1) the login password of 'router'
    2) the 'enable secret' - the password for the CLI command 'enable'
New Password:                         <- router's login password (nothing is shown)
Retype New Password:
enable secret:                        <- password for 'enable' (at least 8 characters)
Confirm enable secret:

==> Quick network setup
  This makes the box a working home/office router right away:
    WAN port  -> internet (DHCP, or PPPoE with your ISP login), firewall + NAT
    LAN port  -> 192.168.1.1, gives addresses to your PCs (DHCP) and DNS
Do the quick setup now? (yes/no) [yes]:

Network ports found in this machine:
   re0      driver re     MAC 00:e0:4c:68:00:01 cable active
   rge0     driver rge    MAC 00:e0:4c:68:00:02 cable no
WAN port (to the internet / ISP modem) [rge0]:
LAN port (to your switch / PCs) [re0]:
Router address on the LAN (network is /24 = 255.255.255.0) [192.168.1.1]:

  How does rge0 connect to the internet?
    dhcp  - the ISP's modem/ONT hands out an address automatically (cable, most fibre/LTE boxes)
    pppoe - your ISP gave you a PPPoE user name and password (DSL, many fibre ISPs)
Connection type (dhcp/pppoe) [dhcp]: pppoe
PPPoE user name (from your ISP) []: jane@isp.example
PPPoE password (not shown):
Retype the PPPoE password:
ISP VLAN ID (only if your ISP gave you one, e.g. 35; empty = none) []:
```

Press **Enter** to accept a suggestion shown in `[...]`. The PPPoE questions only appear if you
answer `pppoe`. Your ISP's letter says whether you need PPPoE (a user name and password for the
internet) and a VLAN ID. If the ISP's modem does the login itself, choose `dhcp` (or put the
modem in bridge mode and choose `pppoe`).

Which port is which? `cable active` means a cable is plugged in, so plug in only the LAN cable to find the LAN
port. With the two Realtek cards: `rge0` is the **2.5 Gbit** port (good as WAN for fast
internet) and `re0` is the **1 Gbit** port.

Three passwords exist now. Write them down:

| Password | Used for |
|---|---|
| root | FreeBSD shell (console or `ssh root@...`) |
| router | logging in to the CLI (console or `ssh router@...`) |
| enable secret | the `enable` command inside the CLI |

---

## Part 5 - First login

After the reboot, the screen shows `login:`.

**On the router's own screen:**

```text
login: router
Password:
router1>                       <- this is the CLI (user mode)
router1> enable
Password:                      <- the enable secret
router1# show ip interface brief
router1# show running-config
```

Log in as `root` instead to get the normal FreeBSD shell.

**From a PC** (if you did the quick setup): plug the PC into the **LAN** port. It gets an
address automatically (192.168.1.50 or higher). Then:

```sh
ssh router@192.168.1.1         # the CLI
ssh root@192.168.1.1           # the FreeBSD shell
```

On Windows use PowerShell (it has `ssh`) or PuTTY.

**Check the internet:** connect the WAN port to your ISP modem, then in the CLI:

```text
router1# show ip interface brief     <- rge0 (DHCP) or dialer1 (PPPoE) has an address from the ISP
router1# show pppoe session          <- PPPoE only: Session should say UP
router1# ping 1.1.1.1
router1# ping freebsd.org
```

If PPPoE stays `DOWN`, see [EXAMPLES.md section 8.6](EXAMPLES.md#86-when-the-connection-does-not-come-up).

**Next:** [EXAMPLES.md](EXAMPLES.md) shows everything step by step: VLANs, DHCP, NAT and port
forwarding, firewall, routing.

---

## Part 6 - Giving the image to others

* Give them the `.img` file (and `.iso` if they want to test in a VM) and `CHECKSUMS.SHA256`.
* They check the download before writing it:
  * Linux: `sha256sum -c CHECKSUMS.SHA256` (shows `OK`; a missing `.iso` can be ignored)
  * Windows PowerShell: `Get-FileHash freebsd-router-...img` and compare with the file
  * FreeBSD/macOS: `sha256 freebsd-router-...img` / `shasum -a 256 freebsd-router-...img`
* Then they follow **Part 3, 4 and 5** of this guide.
* The image contains **no passwords and no keys**. Each installation asks for its own
  passwords, and each router creates its own SSH host keys.
* Build a new image every month or two, so new installations get the latest security
  fixes (Part 7).

---

## Part 7 - Updates

**New image:** on the build machine run the same command again. It downloads the newest
`stable/15` source and rebuilds:

```sh
cd /root/freebsd-router-cli
sh image/build-image.sh
```

**Updating a router that is already installed** (simplest way: reinstall and restore the
configuration):

1. Save the router's configuration to your PC:
   ```sh
   scp root@192.168.1.1:/var/db/router-cli/startup-config.json .
   ```
   (In the CLI, run `write memory` first so the startup-config is current.)
2. Install from the new USB stick (Part 4). You can skip the quick setup.
3. Copy the configuration back and load it:
   ```sh
   scp startup-config.json root@192.168.1.1:/tmp/
   ssh root@192.168.1.1 'install -o root -g router -m 0640 /tmp/startup-config.json /var/db/router-cli/'
   ssh router@192.168.1.1
   router1> enable
   router1# copy startup-config running-config
   ```
   This needs the same network port names (`re0`, `rge0`, ...) as before. The enable secret
   becomes the old one again.

**Only the packages** (python, dnsmasq, FRR), if the router has internet:
`pkg upgrade` as root. If python's version changes (for example 3.12 → 3.13), run
`sh /usr/local/share/router-cli/setup/install.sh` afterwards.

`freebsd-update` does **not** work for `stable/15` systems; it only updates official
RELEASE versions. Always update FreeBSD itself with a new image.

---

## When something goes wrong

The script stops with `ERROR:` and shows the last 40 lines of the log. The full logs are in
`/usr/obj/router-image/logs/`.

| Message / problem | Fix |
|---|---|
| `this must run on FreeBSD` | You started it on Linux/Windows. Use the FreeBSD build machine (Part 1). |
| `run as root` | Type `su -` first. |
| `FreeBSD 15 or newer is needed` | Install FreeBSD 15 on the build machine (Step 1.1). |
| `git is missing` | `pkg install -y git-lite` |
| `only N GB free` | Give the VM a bigger disk, or delete old builds: `rm -rf /usr/obj/usr` |
| `/usr/src contains source code that is not a git checkout` | You installed FreeBSD's "src" set. Move it: `mv /usr/src /usr/src.old`, run again. |
| step `git-clone` / `git-update` failed | Internet or DNS problem. Test with `ping -c3 git.freebsd.org`, then run again. |
| step `buildworld` failed with `Killed` or `out of swap` | Not enough memory: run again with `-j 2`. |
| step `buildworld` failed otherwise | Often a broken source update. Run with `-f` (full rebuild). If it still fails, wait a day: `stable/15` is fixed quickly. Or use the previous source: `cd /usr/src && git log --oneline -3`. |
| step `pkg-update` / `pkg-fetch` failed | Internet problem, or a package is temporarily missing for this architecture. Run again later. |
| build seems stuck | Run `tail -f` on the log shown. If new lines appear, it is working. `top` shows `cc`/`ld` working. |
| Router does not boot from the stick | Boot menu key, disable Secure Boot, try another USB port. Write the stick again, using DD mode in Rufus. |
| Installer: "Distribution Sets" or "Packages"? | **Distribution Sets**. |
| Router setup stops with an error | It offers `Try again? [Y/n]`. If it fails again, answer `n`: FreeBSD itself is installed. After the reboot, log in as root and run `sh /usr/local/share/router-cli/setup/install.sh` and `setup_accounts.sh`, or simply reinstall. |
| Only one network port is listed | The second card is missing or not supported. Run `pciconf -lv | grep -B3 network` as root to list the cards. |
| `ssh router@192.168.1.1` does not answer | The PC must be on the **LAN** port and get a 192.168.1.x address. On the router console: `show ip interface brief`. |
| Forgot the enable secret | Log in as **root** and run `FORCE_INIT=1 sh /usr/local/share/router-cli/setup/setup_accounts.sh` (this also resets the configuration). |
| Forgot the root password | Boot, choose "Single user" in the boot menu (option 2), press Enter at the shell question, then `zfs set readonly=off zroot` (ZFS) or `mount -u /` (UFS), then `passwd root`. |

---

## Appendix A - What the build script does

[`image/build-image.sh`](../image/build-image.sh) only uses FreeBSD's own tools:

1. **Checks**: FreeBSD ≥ 15, root, git, ≥ 30 GB free, RAM.
2. **Source**: `git clone --depth 1 --branch stable/15 https://git.FreeBSD.org/src.git /usr/src`
   (or `git fetch` + `checkout` when it already exists).
3. **World**: `make buildworld` with `SRCCONF=image/src.conf` and `__MAKE_CONF=/dev/null`. The
   build machine's own `/etc/src.conf` and `/etc/make.conf` are ignored, so the image is the
   same whatever the build machine is configured with.
4. **Kernel**: `make buildkernel KERNCONF=GENERIC`. The GENERIC kernel supports the most
   hardware; `pf`, `pflog`, `if_vlan` and `if_rge` are loaded as modules.
5. **Release staging**: `make -C /usr/src/release disc1 NOPKGBASE=1 NOSRC=1 NOPORTS=1 NOPKG=1`.
   This creates `base.txz` + `kernel.txz` (what gets installed) and the live installer system,
   without source, ports, debug files or Wi-Fi firmware.
6. **Packages**: with a private package database for the *target* ABI
   (`FreeBSD:15:amd64` or `FreeBSD:15:aarch64`): `pkg fetch -d pkg python3 python3XX
   py3XX-prompt-toolkit dnsmasq doas frrNN`, then `pkg repo` makes it a package repository.
   The build machine's own installed packages are not touched.
7. **Router files** go into the installer:
   * `/usr/freebsd-router/` on the stick: the project, the package repository and `build.env`
   * `/usr/libexec/bsdinstall/local.post-configure`: a standard bsdinstall hook. It runs
     after FreeBSD is installed, copies everything into the new system and runs
     [`image/installer/postinstall.sh`](../image/installer/postinstall.sh) there (chroot)
   * `/etc/rc.local` gets `BSDINSTALL_SKIP_FIRMWARE/USERS/SERVICES` (screens the router setup
     replaces)
   * every added file is listed in the `METALOG` ([`image/metalog.sh`](../image/metalog.sh)),
     because the image is built from that list
8. **Images**: `make -C /usr/src/release disc1.iso memstick.img`. These are hybrid
   BIOS + UEFI on amd64 and UEFI on arm64. Then they are renamed and their SHA-256 checksums
   are written.

`postinstall.sh` (runs on the router during the installation):

1. `setup/install.sh` with `PKG_REPO_DIR=...`: bootstraps pkg from the stick, installs the
   packages from the offline repository, installs router-cli, and turns on the `rge` driver
   (`if_rge_load="YES"` in `/boot/loader.conf`) when a Realtek 2.5G card is present. It also
   sets up netisr packet processing on all cores: `net.isr.dispatch=deferred` in
   `/etc/sysctl.conf`, `net.isr.maxthreads=<number of cores>` and `net.isr.bindthreads=1` in
   `/boot/loader.conf` (these two are boot-time tunables; `NETISR_TUNING=no` skips all three)
2. `setup/setup_accounts.sh`: the `router` account (login shell = the CLI) and the enable secret
3. `setup/harden_sshd.sh` (with `SSHD_RESTART=no`): router → CLI only; root → password or key
4. optional quick setup: [`image/installer/quick_setup.py`](../image/installer/quick_setup.py)
   builds the CLI script from [`quick-setup.cli`](../image/installer/quick-setup.cli) and the DHCP
   or PPPoE WAN part (the PPPoE password goes in as a type 7 string, never through the
   shell). The real CLI checks it in a sandbox. It is then installed with
   `router-apply init` (`write_files`), which writes `rc.conf`, `pf.conf`, `dnsmasq.conf` (and
   `/etc/ppp/ppp.conf` for PPPoE), so the first boot already routes.

Doing the same by hand (for experts):

```sh
cd /usr/src
export SRCCONF=/root/freebsd-router-cli/image/src.conf __MAKE_CONF=/dev/null
make -j$(sysctl -n hw.ncpu) buildworld buildkernel                  # add TARGET=arm64 TARGET_ARCH=aarch64 for ARM
R="make -C release NOPKGBASE=1 NOSRC=1 NOPORTS=1 NOPKG=1"
$R clean obj && $R disc1
#   ... add files to $(make -C release -V .OBJDIR)/disc1 + lines to disc1/METALOG ...
$R disc1.iso memstick.img
```

---

## Appendix B - Customising the image

All of these are files in the project; edit them, then run `build-image.sh` again.

| What | Where |
|---|---|
| Remove/keep FreeBSD components | [`image/src.conf`](../image/src.conf) (`man src.conf`). Keep the components listed under "KEEP". `WITHOUT_X=anything` removes X, so delete a line to keep the component. Changing it triggers a full rebuild. |
| What the quick setup configures | [`image/installer/quick-setup.cli`](../image/installer/quick-setup.cli) plus the WAN part [`quick-setup-wan-dhcp.cli`](../image/installer/quick-setup-wan-dhcp.cli) / [`quick-setup-wan-pppoe.cli`](../image/installer/quick-setup-wan-pppoe.cli): normal router-cli commands, e.g. add `ip dhcp pool` options or a `banner motd` |
| Questions during installation | [`image/installer/postinstall.sh`](../image/installer/postinstall.sh) |
| Own kernel configuration | Put a file in `/usr/src/sys/amd64/conf/ROUTER` (start with `include GENERIC` and `ident ROUTER`), then `sh image/build-image.sh -k ROUTER`. Not needed for the Realtek cards. Note that `-u` is needed if the file is not committed to git, or it is lost on update; better keep it outside `/usr/src` and copy it in. |
| Newer packages (e.g. latest FRR) | Packages come from the build machine's pkg configuration ("quarterly" by default). For "latest": `mkdir -p /usr/local/etc/pkg/repos && echo 'FreeBSD-ports: { url: "pkg+https://pkg.FreeBSD.org/${ABI}/latest" }' > /usr/local/etc/pkg/repos/FreeBSD.conf` on the build machine. |

The FreeBSD tools behind this: `build(7)`, `release(7)`, `src.conf(5)`, `bsdinstall(8)`
(section *Scripting*, hooks), `pkg-fetch(8)`, `pkg-repo(8)`.

---

## Appendix C - Other ways to install router-cli

**On an existing FreeBSD 15 system with internet** (e.g. official 15.1-RELEASE):

```sh
# as root, project copied to /root/freebsd-router-cli
cd /root/freebsd-router-cli
sh setup/install.sh            # packages from the internet + router-cli
sh setup/setup_accounts.sh     # 'router' account + enable secret
sh setup/harden_sshd.sh        # SSH: router -> CLI, root -> shell
```

Realtek RTL8125B on 15.0/15.1-RELEASE: those versions do **not** have `rge`, so
`install.sh` installs Realtek's driver package `realtek-re-kmod` (`REALTEK_DRIVER=auto` →
`vendor`). Both cards are then called `re0`/`re1`. On `stable/15` or 15.2+ it uses the built-in
`rge` (`re0` + `rge0`). Force a choice with `REALTEK_DRIVER=vendor` or `REALTEK_DRIVER=native`.
If the 2.5G port stalls under load, use `no hardware-offload` on it in the CLI.

**Without internet:** copy the `packages` directory from an image build
(`/usr/obj/router-image/pkg-amd64/repo`) to the machine and run
`PKG_REPO_DIR=/path/to/repo sh setup/install.sh`.

**On a system installed with pkgbase** ("Packages" in the installer): `install.sh` works the
same way. Update FreeBSD with `pkg upgrade` and check for `*.pkgnew` files in `/etc` instead of
using `etcupdate`.

---

## Appendix D - RISC-V and FreeBSD 14

**RISC-V (later):** FreeBSD builds for `TARGET=riscv TARGET_ARCH=riscv64`, but the release
tools have no ISO/USB installer for it. RISC-V boards boot SD-card images
(`make -C release ... riscv` / `crochet`-style) instead. Python, dnsmasq and doas exist as
`FreeBSD:15:riscv64` packages. Support can be added to `build-image.sh` as an SD-card image.
The script currently refuses `-a riscv64` with a clear message.

**FreeBSD 14:** use `-b stable/14` on a FreeBSD 14 build machine. Remove the FreeBSD 15
check in `build-image.sh` first. 14 has no `rge` driver, so the RTL8125B needs the
`realtek-re-kmod` package. Add it to the package list in step 6 of the script, and both cards
become `re0`/`re1`. Older 14.x package sets use Python 3.11; this is detected automatically.
