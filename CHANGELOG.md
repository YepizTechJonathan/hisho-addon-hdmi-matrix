# Changelog

## 0.2.1

- Found on your network finds both instr-json models: the HDP-MXB88DA's page names nothing and its API takes about 3 s, so the API path is asked (200, text/plain) with a longer wait.

## 0.2.0

- Find it: the page lists HDMI matrices on your network by their web page (port 80, one GET). The instr-json model (its page names HDP-MXB88D70M or `cgi-bin/instr`) comes with its driver filled in; another matrix's page fills in only the address. Use this fills the form.

## 0.1.5

- Every field on its setup form says in plain words what it is, and links to where to get it (addon.yaml `help:`, shown by Hisho's dashboard).

## 0.1.4

- README says what you need (addon.yaml you_need) and how to use it; the README check requires both.

## 0.1.3

- **Wake screen** (page button, `matrixctl wake OUT`): turns one output's signal off and on again, like power-cycling its monitor, for a monitor that stays dark when its PC wakes. The signal is always turned back on, and checked.

## 0.1.2

- The matrix keeps one reply slot, so with its web UI open (it polls every 4 s) a reply can be another client's. Reads are now asked again; writes are still sent once and checked by reading the matrix back. Before, a crossed reply stopped `matrixctl` after a write had already been sent.
- `matrixctl`: `--yes`, `--address` and `--driver` work anywhere on the line (`matrixctl main route 2 7 --yes`, as the README shows); `--address 192.0.2.50` was taken as a matrix name.
- Login (only sent when an entry has a password) uses `username`, as the matrix's web UI does.

## 0.1.1

- README: the page's buttons from a terminal with `hisho addon do`.
- Test fixture renamed after the protocol and firmware it was recorded from (no machine names in the repository). No change to what the add-on does.

## 0.1.0

- First version: routing, monitors detected, input signal and EDID per input for an 8x8 HDMI matrix (cgi-bin/instr API, firmware V1.08.16); watched outputs raise a problem when dark; an input copying its EDID from an output with no monitor is pointed out (the matrix then sends its 1080p fallback EDID); changes are journaled.
- Buttons and `matrixctl`: switch input, change EDID, recall / save preset, rename; each asks first and re-reads the matrix to say whether it took. Factory reset, reboot, network and firmware are never sent.
