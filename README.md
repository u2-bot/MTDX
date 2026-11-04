# MTDX

Play your own copy of **Pokémon Mystery Dungeon: Rescue Team DX** on Windows. Drop in your dump, run one program once, play.

MTDX is the Windows player of the RTDX PC project (the game rebuilt as a native Unity 2018.3 Windows game: its C# code
and its native C++ code rebuilt for x64) plus a converter that turns **your own** game dump into the data that player reads.
No Unity, no setup scripts, no game files downloaded: everything game-related comes from the dump you made, and stays on
your PC.

## What you need

- Windows 10 or 11, 64-bit, a DirectX 11 graphics card
- About **4 GB** of free disk space where you put MTDX
- 8 GB of RAM recommended; with less, MTDX runs fewer conversion workers and takes longer
- Your own dump of **Pokémon Mystery Dungeon: Rescue Team DX** for Switch **with the version 1.0.2 update applied**,
  extracted to a folder: the folder that holds **`romfs`** (with `Data` inside) **and `exefs`** (with `main` and `sdk`
  inside). Extract the base game and the 1.0.2 update together (the update's files over the base game's). Both folders are
  needed: `romfs` holds the game's data, `exefs` the game program, whose data tables the rebuilt native code reads.
  Older versions (1.0.0, 1.0.1) are not supported.

## Play

1. Download the release and unzip it to a folder of its own.
2. Start **MTDX.exe**.
3. Press **Browse...** and pick your dump folder (the folder with `romfs` and `exefs` in it - MTDX also finds it one or two
   levels down).
4. Press **Convert**. MTDX checks that the dump is Rescue Team DX version 1.0.2, then converts it - a few
   minutes on a desktop, longer on a small laptop. You can close it and start it again; it continues where it stopped.
5. Press **Play**.

After that, start **RTDX.exe** (or MTDX.exe, then Play) whenever you want to play. Your dump is only read; MTDX never
changes it. Keep it: a later MTDX update may need to convert again.

## Updates

MTDX keeps itself up to date. At every start it looks for a new version (on this project's GitHub release page); when
there is one, it downloads it, closes, copies it into its folder and starts again by itself. It then sees what the update
changed and makes only those files again from the dump you used last time (an update that changes the conversion itself
converts everything again). Then press Play.

- Untick **Update MTDX automatically** in the window to stop that; **Check for updates** looks (and asks first) whenever
  you press it.
- Every update is checked against a signature that only MTDX's own releases carry: a changed or damaged download is never
  installed. The check reads one small file from github.com; nothing else is sent.
- While the game is running, MTDX does not update; it does so at its next start.
- By hand (always possible, and the way up from MTDX 0.1.0, which cannot update itself yet): download the new release,
  unzip it **over your MTDX folder** and let it overwrite the files, then start **MTDX.exe** once.

Your game data, saves and settings stay through every update: saves are not stored in the MTDX folder, and the game data is
made again only where the update needs it.

## Saves and settings

- Saves: `%USERPROFILE%\AppData\LocalLow\RTDX\save\` (the **Saves folder** button opens it). Copy that folder to keep a
  backup.
- Settings: `%USERPROFILE%\AppData\LocalLow\MTDX\Pegasus\pc_settings.ini` (the **Settings folder** button), changed
  in the game: **Options > Basic 1**, the first rows of the list (it scrolls):

| Row | Choices |
|---|---|
| Display Mode | Windowed / Borderless / Full Screen |
| Resolution | your monitor's resolutions |
| Frame Rate | 30 (as on the Switch) / 60 / 90 / 120 / 144 / 165 / 240 / Unlimited. The game itself always steps 30 times a second; above 30 the frames in between are drawn smoothly |
| V-Sync | On / Off |
| Keyboard Keys / Controller Buttons | A opens the list of actions; in it A = press the new key or button (Esc = cancel), X = clear, Y = defaults, B = back |

  Everything is applied when you leave Options and answer YES to the save question.
- **Fast-forward**: left-stick click (controller) or F (keyboard); each press steps 1x -> 2x -> 4x -> 8x -> 1x.

## Controls (default)

- Controller (XInput pads: Xbox and most PC pads; the buttons by their Switch positions): east = A, south = B, north = X, west = Y,
  LB / RB = L / R, LT / RT = ZL / ZR, Start = +, Back = -, d-pad, left stick = move, right stick = camera.
- Keyboard: Z / Space = A, X / Backspace = B, C = X, V = Y, Q / E = L / R, 1 / 3 = ZL / ZR, Return = +, Tab = -, arrows =
  d-pad, WASD = left stick, IJKL = right stick, F = fast-forward. Mouse left button = touch.

## When something goes wrong

- Logs: `%USERPROFILE%\AppData\LocalLow\MTDX\Pegasus\output_log.txt` (the game) and
  `RTDX_Data\Plugins\pcsdk.log` (the native layer); attach them to a report.
- The MTDX window explains every problem it finds with the dump and what to do about it.
- From a command prompt: `MTDX.exe status`, `MTDX.exe check DUMP`, `MTDX.exe convert [DUMP] [--workers N]`,
  `MTDX.exe play`, `MTDX.exe update [--check]`.
- An update that did not finish (the window says so): download the release and unzip it over the MTDX folder by hand;
  **Check for updates** also tries again.

## Legal

MTDX contains no data of Pokémon Mystery Dungeon: Rescue Team DX: the converter makes the game data from your own dump on
your PC. Pokémon is a trademark of Nintendo, Creatures and Game Freak; MTDX is not affiliated with or endorsed by them, The
Pokémon Company or Spike Chunsoft. See `THIRD-PARTY-NOTICES.md` for the software by others a release contains.
