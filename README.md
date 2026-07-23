# claudeink

Claude usage limits on an Inky pHAT, in the spirit of [octowait](https://github.com/simonhearne/octowait).

Lays out the same three windows as Claude Code's own `/usage` report — **Current session**,
and under a *Weekly* divider, **All models** and **Fable** — each with a pill progress bar,
its reset time, and a percentage. Clock top right.

```
 Plan usage  Max 5x                        15:33
 ─────────────────────────────────────────────────
 Current session       ▰▰▰▰▰▰▰▱▱▱▱▱▱        55%
 Resets in 2 hr 19 min
 WEEKLY ──────────────────────────────────────────
 All models            ▰▰▰▰▱▱▱▱▱▱▱▱▱        33%
 Resets Sat 21:59
 Fable                 ▰▰▰▰▰▰▱▱▱▱▱▱▱        45%
 Resets Sat 22:00
```

![photo of claudeink running](claudeink.jpg)

## Refresh

Refreshes every 5 minutes (`REFRESH_MINUTES=5`), sleeping to the boundary rather than
sleeping a fixed 300s, so the clock doesn't drift or skip a minute. That's ~288 panel
updates a day — fine for a pHAT, but also defaults to `QUIET_START=20 QUIET_END=7` to stop refreshes overnight (pi local timezone).

Session reset is shown as a live countdown, weekly resets as day + time, matching the
native report.

## Hardware note

Assumes a **Pi Zero W v1.1**. You might need to tweak things if using a different model.

## Install

```bash
curl https://get.pimoroni.com/inky | bash      # say no to the extra examples
git clone <your-repo> ~/claudeink && cd ~/claudeink
sudo apt-get install -y python3-pip fonts-dejavu-core
pip3 install -r requirements.txt
```

Enable SPI and I2C via `sudo raspi-config` if the Pimoroni installer didn't.

## Credentials

The script reads `~/.claude/.credentials.json` — the same file Claude Code maintains.
Copy it over from a machine where you're logged in:

```bash
scp ~/.claude/.credentials.json pi@raspberrypi.local:~/.claude/.credentials.json
ssh pi@raspberrypi.local chmod 600 ~/.claude/.credentials.json
```

**Token refresh:** Claude Code isn't running on the Pi, so nobody is refreshing the access
token for you — it'd expire in hours. `refresh_token()` handles this itself using the
`refreshToken` field. That endpoint and client id are reverse-engineered from the Claude
Code client, not documented API, so treat them as the most fragile part of this project.
If refresh starts failing you'll see it in the journal and the clock gets a `!` prefix to
show the data is stale; re-copy the credentials file to recover.

Nothing about `/api/oauth/usage` is a supported interface either. It can change without
warning.

## Test

```bash
python3 run.py --demo --once --png     # synthetic data, writes frame.png, no hardware needed
python3 run.py --once                  # one real frame to the panel
```

If no Inky is detected it falls back to writing `frame.png`, which is handy over ssh.

## Run as a service

```bash
sudo cp claudeink.service /etc/systemd/system/
sudo systemctl enable --now claudeink
journalctl -fu claudeink
```

## Config

All via environment (set them in the unit file):

| Var | Default | Notes |
| --- | --- | --- |
| `REFRESH_MINUTES` | `1` | minutes between panel refreshes |
| `PLAN_LABEL` | *(empty)* | e.g. `Max 5x`, drawn beside the title |
| `WARN_AT` | `80` | % at which a bar fills red instead of black |
| `QUIET_START` / `QUIET_END` | *(unset)* | hours to pause refreshing, e.g. `23` / `7` |
| `FLIP` | `0` | set `1` to rotate 180° |
| `FONT_REGULAR` / `FONT_BOLD` | DejaVu | TTF fonts available on the system |
| `CREDENTIALS` | `~/.claude/.credentials.json` | |

Times are rendered in the Pi's local timezone — `sudo timedatectl set-timezone Europe/London`
if you haven't already.

## Window keys

`ROWS` at the top of `run.py` maps each bar to a list of candidate API keys, tried in
order, with a fuzzy fallback. The Fable row tries `seven_day_fable` then `seven_day_opus`
— I couldn't verify which key that window actually uses. Run `--once` and if Fable shows
`--`, dump the raw payload and add the real key to that list.

## Layout

Sizes derive from `inky.resolution`, so it lays out correctly on both the 250×122 and
212×104 pHATs. Labels auto-shrink to fit their column rather than overflowing into the bar.

## Power saving

Run these on the pi to reduce compute / power consumption:

```bash
sudo /opt/vc/bin/tvservice -o
echo 'dtoverlay=disable-bt' | sudo tee -a /boot/config.txt
echo 'dtparam=act_led_trigger=none' | sudo tee -a /boot/config.txt
```
