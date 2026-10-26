# Changelog

What changed in each release of the S.E.A.M. Price Scanner. The newest is first.
The download page at https://www.seamindustries.com/scanner shows these notes, and each
GitHub release starts with its own section, so add one here before tagging a release
(the release build stops if the section for the new version is missing).

## 1.4.0 (2026-10-26)

- **Check unclear prices.** When a price cannot be read with confidence it is not sent on
  a guess. It is listed as *Unclear* with the scanner's best reading, and **Check price**
  next to the result lets you confirm it or type the right price. In the console version:
  `confirm <name> [price]`.
- **Each price is sent once.** The same commodity at the same terminal and tab is sent at
  most once every 90 seconds, however many screenshots show it. Repeats are listed as
  *Already sent*. S.E.A.M. enforces the same rule on its side.
- **Newer terminal layout.** Reads the *Local Market Value* tab as the sell tab, and long
  names that run straight into the stock figure (such as Recycled Material Composite).
- **Unknown commodities are named.** A commodity S.E.A.M. does not list yet now shows in
  the results instead of being left out without a word.
- **All commodity terminals.** S.E.A.M. now lists every live commodity terminal (114),
  including Lorville's Trade Center. Where a city or station has more than one terminal,
  the scanner no longer guesses which one from the location name alone.

## 1.3.1 (2026-10-23)

- **Windows installer.** `SEAM-Price-Scanner-Setup.exe` installs per user with no
  administrator prompt, adds a Start menu entry and an uninstaller in Settings > Apps, and
  upgrades in place. The portable zip is still available. Both downloads are attested.

## 1.3.0 (2026-10-22)

- **First public release.** Reads a commodity terminal from a screenshot of the game
  window with the text recognition built into Windows and reports the prices to
  S.E.A.M. Sign-in is encrypted with your Windows account, only the game window is
  captured, and it only talks to S.E.A.M. over HTTPS. Releases are built by GitHub with
  a signed build-provenance attestation.
