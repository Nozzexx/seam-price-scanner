# S.E.A.M. Price Scanner

A small Windows app for [S.E.A.M. Industries](https://www.seamindustries.com) members and
friends. At a Star Citizen commodity terminal, press a hotkey and it reads the prices on
screen and reports them to the S.E.A.M. price board, so everyone sees current prices
without typing them in.

This repository is the complete source of the app. Every download on the
[Releases](../../releases) page is built from this code by GitHub, not on anyone's PC, and
comes with a signed record proving which commit it was built from (see
[Checking a download](#checking-a-download)).

## What it does, step by step

1. **Waits for its two hotkeys.** Ctrl+Shift+F9 (Buy tab) and Ctrl+Shift+F10 (Sell tab).
   It registers just those two combinations with Windows (`RegisterHotKey`). It does not
   hook the keyboard and cannot see anything else you type.
2. **Takes one screenshot of the game window.** Only when you press a hotkey, and only the
   Star Citizen window. It checks that the window really belongs to `StarCitizen.exe`, so a
   browser tab about the game never counts. If the game is in a window, the rest of your
   screen is not captured.
3. **Reads it on your PC.** Text recognition is done by the OCR engine built into Windows
   (`Windows.Media.Ocr`). Nothing is sent anywhere at this point.
4. **Matches names to S.E.A.M.'s commodity list.** A price that is not read cleanly is
   skipped, never guessed. Shop kiosks (Pharmacy, armour and so on) are recognised and
   nothing is sent.
5. **Sends the prices to your S.E.A.M. account**, together with the screenshot as evidence
   for moderators. If the game window was not found, no screenshot is sent. You can turn
   screenshots off entirely (see [Settings](#settings)).

## What it never does

- It never reads or changes the game's memory or files and never touches its network
  traffic, so to the game and its anti-cheat it is no different from pressing Print Screen.
- It never runs in the background without a window, starts with Windows, or updates
  itself.
- It never saves your password. Sign-in keeps a renewable token, encrypted with Windows
  DPAPI so only your Windows account on that PC can read it.
- It talks to exactly one server: the S.E.A.M. database at
  `ptumimzylfskitbnlfmf.supabase.co`, over HTTPS only. (It refuses to send your sign-in over
  an unencrypted connection.)

## Exactly what is sent

| When | What | Where it goes |
| --- | --- | --- |
| Start-up | Requests for the commodity list and the terminal list | Public data, no sign-in |
| Sign in | Your S.E.A.M. email and password | Supabase Auth; you get a token back |
| Each scan | Terminal, Buy or Sell, and for each commodity: name, price, stock and stock level | `submit_price_scan`, under your account |
| Each scan | The screenshot of the game window, as a JPEG (at most 1920 px wide) | Private storage, readable only by you and S.E.A.M. admins; deleted after 30 days |
| Undo, Move, Recent scans | Your own scan's ID | `withdraw_price_scan`, `move_price_scan`, `my_price_scans` |

What the screenshot can contain: whatever the game shows at that moment, including your
in-game wallet balance, your character's name on some screens, and in-game chat or
overlays (such as Discord's) if they are open. Close them first if you would rather they
were not included, or turn screenshots off.

All the network code is the `Site` class in [`seam_scanner.py`](seam_scanner.py), about
100 lines using only Python's standard `urllib`.

## What the server checks

The app is not trusted. Every rule is enforced by the database, whatever a modified
copy of this app might send:

- Unknown commodities and terminals are refused.
- Prices far from the recent median are held for review. Members' reports count at once;
  everyone else's wait for an officer.
- At most 40 scans an hour, 80 prices per scan, and the same commodity at the same
  terminal three times in ten minutes.
- Screenshots go to a folder named after your account, which only you and admins can
  read.

## Install

Download `SEAM-Price-Scanner-<version>.zip` from [Releases](../../releases), unzip it
anywhere and run `SEAM Price Scanner.exe`. Then press **Sign in** with your S.E.A.M. email
and password. (Signed up with Discord? Set a password with "Forgot password" on the site.)

Windows may say "Windows protected your PC" the first time, because the app is new and
not yet code-signed. Choose **More info**, then **Run anyway**, after checking the
download as below if you like.

## Checking a download

Each release lists the zip's SHA-256. In PowerShell:

```powershell
Get-FileHash .\SEAM-Price-Scanner-1.3.0.zip -Algorithm SHA256
```

The result must match the release page. To prove the zip was built by GitHub from this
repository (and not swapped afterwards), with the [GitHub CLI](https://cli.github.com):

```powershell
gh attestation verify .\SEAM-Price-Scanner-1.3.0.zip --repo Nozzexx/seam-price-scanner
```

It prints the exact commit and workflow run that produced the file.

## Run it from source instead

Install [Python 3.12](https://www.python.org/downloads/), download this repository, and
double-click `run-scanner.bat`. The first run creates a private Python environment in
`.venv` and installs the exact versions in [`requirements.txt`](requirements.txt).
`run-scanner.bat --console` starts a text-only version.

To build the .exe yourself: `build-exe.bat`.

## Settings

`%APPDATA%\SEAM\scanner\config.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `upload_screenshot` | `true` | `false` sends prices without the screenshot (they may then wait longer for review) |
| `hotkey_buy`, `hotkey_sell` | `ctrl+shift+f9`, `ctrl+shift+f10` | The two hotkeys |
| `min_match` | `0.84` | How closely a name must match a commodity |
| `terminal` | | The terminal used when its name is not on screen |

The same folder holds `last-scan.png` (the most recent capture, never uploaded unless it
was sent with a scan), `scans.log` (what was sent and the result) and `queue\` (scans
waiting to be sent while offline). Delete the folder to remove everything.

## Using it

1. Open a commodity terminal in game and pick the Buy or Sell tab.
2. Press the matching hotkey. One beep means it started; a rising two-tone beep means the
   prices were sent; a falling tone means something went wrong (the window says what).
3. Wrong terminal or tab? **Undo** withdraws the scan, and **Move to terminal...** files it
   at the right one, for 24 hours.

## Files

| File | What it is |
| --- | --- |
| `seam_scanner.py` | Everything that matters: capture, OCR, matching, the server calls, hotkeys |
| `seam_scanner_gui.py` | The window (tkinter) |
| `requirements.txt` | Exact versions of the four libraries used: `mss` (screenshots), `Pillow` (images), `winrt-*` (Windows OCR) |
| `build-exe.bat`, `packaging/` | How the .exe is built (PyInstaller) |
| `.github/workflows/release.yml` | The GitHub build that makes each release |
| `test/` | Real terminal screenshots for testing, with the wallet balance and character name blacked out |
| `assets/` | Icon, logo and the Geo font (SIL Open Font License) |

## Reporting a security problem

See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE): free to read, use, change and share, with credit. The Geo font in
`assets/` keeps its own SIL Open Font License, and the test screenshots show Star
Citizen, which belongs to Cloud Imperium Games.

Star Citizen is a trademark of Cloud Imperium Games. This is a fan-made tool and is not
affiliated with or endorsed by Cloud Imperium Games.
