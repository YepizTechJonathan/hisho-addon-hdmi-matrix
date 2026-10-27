# HDMI matrix (Hisho add-on)

## What it does

Watches your HDMI matrix switches and lets you drive them from Hisho: one entry per matrix, each
with a `driver` for the protocol it speaks. `instr-json` is the "18Gbps 8x8 HDMI & Matrix" with a
web UI at `http://<address>/` (HDP-MXB88D70M, firmware V1.08.16); a matrix that speaks something
else needs a driver class added to `DRIVERS` in `matrix.py` (its docstring says what it must offer). Its page shows, per output,
which input it shows, whether a monitor is detected and whether there is a picture; per input,
whether it has a signal and which EDID it gives its PC. It points out inputs that copy their EDID
from an output with no monitor detected: the matrix then hands the PC its own 1080p fallback EDID
("HDMI MATRIX"), and the PC drops to 1080p or loses the screen.

Outputs you list under `watch` raise a problem on Needs you when they go dark. Changes seen on the
matrix (routing, monitors coming and going, input signal, EDID, names) are journaled as
`hdmi-matrix-change`; buttons pressed on the page are journaled by Core.

Buttons: switch an output's input, wake an output's screen, change an input's EDID, recall or save a
preset. Each asks first. **Wake screen** turns that output's signal off and on again, which is what
power-cycling a monitor does: for a monitor that stays dark when its PC wakes up (`matrixctl main wake 8`). Factory reset, reboot, network settings, EDID upload and firmware upload are never sent:
the client only knows the commands it lists.

From a terminal, `matrixctl` (put on PATH on the Core machine by the install; or run
`bin/matrixctl` from this folder):

```bash
matrixctl main status
```

`matrixctl list`, `matrixctl main route MainPC2 TopMonitor`, `matrixctl main edid MainPC1 "copy 8"`,
`matrixctl edid --list`, `matrixctl main preset 3`, `matrixctl main preset save 3`,
`matrixctl main rename output 2 DeskLeft`. The first word names the matrix (an entry in the add-on's
settings); with a single entry it can be left out, and `--address` (with `--driver`) reaches one that
isn't set up. Every write says what it will do and asks; `--yes` answers for scripts.

The page's buttons can also be pressed with Hisho's own command, e.g.
`hisho addon do hdmi-matrix main out-7 route --input MainPC2` (output 7 shows input MainPC2).

## What it needs

- You need:
  - An HDMI matrix switch on your network that speaks a supported protocol (`instr-json` today)
- Runs on: the Core machine, inside Hisho Core
- One entry per matrix, each with:
  - `address` (str, required)
  - `driver` (list(instr-json), optional)
  - `watch` (str, optional)
  - `user` (str, optional)
  - `password` (password, kept as a local secret, optional)
  - `note` (str, optional)

## How to install

On the dashboard: **Add-ons > Store > HDMI matrix > Install**. Or from a terminal:

```bash
hisho addon install hdmi-matrix
```

Hisho takes the newest version from this repository, copies it to the machine it runs on and turns
it on. Updates show as **Update available** on its Store card; **Roll back** returns to the version
before. The Core machine must reach the matrix on the LAN (plain HTTP, port 80).

## How to use

Its page (**Add-ons > HDMI matrix**) shows each output's input, monitor and picture, and each input's signal and EDID. **Switch** picks an input for an output; **EDID** sets what a PC is told about its monitor. Both ask first.

## Settings

On its page (**Add-ons > HDMI matrix**), or in `~/Hisho/System/Config/Addons/hdmi-matrix.yaml`:

```yaml
entries:
  main:
    address: 192.0.2.9
    driver: instr-json
    watch: TopMonitor,BotMonitor
```

Each matrix is one more block under `entries:`.

Reads never log in. `user` and `password` are only needed if the matrix refuses changes without a
login: type the password on the page (kept in Core's local secrets file) or give a secret's name
(Infisical / Bitwarden); it is fetched at the moment of a change and only sent to the matrix.

## How to remove

**Add-ons > Store > HDMI matrix > Remove**, or `hisho addon remove hdmi-matrix`. Your settings file
stays, so installing it again picks up where you left off.

## License

Source-available under the [PolyForm Shield License 1.0.0](LICENSE.md): free to use, change and share,
for any purpose except offering a product that competes with Hisho (reselling it, for example).
