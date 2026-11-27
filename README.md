# Moo-Moo porting guide

**Competition write-up:** [COMPETITION.md](COMPETITION.md), covering the strategy, risk controls, validation, the exit backtest and live results.

Updated 23 November 2026. Run the H ETF and Two-vote dashboard locally on Linux, Windows, or macOS.

## Install on a new device

Install Python 3.12 or later with `venv` support. On Debian or Ubuntu, add the matching `python3-venv` package if Python reports that `ensurepip` is missing.

Download `Moo-Moo-Runnable-Package.zip` from the public submission page and extract it. The source repository is private; the public package is self-contained.

```text
unzip Moo-Moo-Runnable-Package.zip -d Moo-Moo
cd Moo-Moo
```

| Operating system | Launch from the repository root |
| --- | --- |
| Linux | `python3 start.py` |
| Windows PowerShell | `py start.py` |
| macOS | `python3 start.py` |

First launch uses internet access to build the device's own Python environment in `moomoo-dual-strategy/.venv`. The launcher opens the dashboard, usually at `http://127.0.0.1:8876/`. Later launches reuse the same dashboard process. Add `--no-browser` to print its URL. On Windows, `py start.py --install-shortcut` adds a desktop shortcut.

For live market data and paper account observations, install the Moomoo CLI on that device and run `moomoo auth login`. Complete consent in the browser and check `moomoo auth status`. Each device creates its own sign-in. The dashboard can open before sign-in. [Official Moomoo CLI installation](https://github.com/MoomooOpen/moomoo-cli#install)

## Keep the Linux dashboard running

```bash
python3 start.py --enable-background --no-browser
systemctl --user status moomoo-dashboard.service
```

Stop any manually launched Moo-Moo dashboard on port 8876 before enabling the service. The service restarts the dashboard and vote monitor after an unexpected exit. User lingering keeps it running through logout and starts it at boot while the machine is powered and awake.

```bash
loginctl show-user "$USER" -p Linger
journalctl --user -u moomoo-dashboard.service
```

If `Linger=no`, use `loginctl enable-linger "$USER"` for boot startup. To disable the dashboard service, run `systemctl --user disable --now moomoo-dashboard.service`.

## Verify the port

- Open the URL printed by the launcher. The competition page should show the H ETF and Two-vote panels after the first refresh.
- Run the launcher again. It should print the same URL.
- For first-run errors, check `moomoo-dual-strategy/private/launcher/setup.log`. For manual dashboard startup errors, check `dashboard.log` in the same folder.
- The Two-vote panel shows signals during the supported market session. The competition paper runner has a separate scheduler and preflight. Any runner migration requires its saved account state and one execution host.

Previous setup notes, strategy specifications, reports, and audits are in the [dated archive](HISTORY.md). [Repository safety contract](moomoo-dual-strategy/AGENTS.md)
