# Third-party notices

MTDX's own code is under the MIT license (`LICENSE`). A release also contains software by others, each under its own
terms:

| Component | Where in a release | Terms |
|---|---|---|
| Unity 2018.3 runtime (player, built-in resources, UnityEngine code) | `RTDX.exe`, `UnityPlayer.dll`, `UnityCrashHandler64.exe`, `GameAssembly.dll`, `RTDX_Data/il2cpp_data/`, `RTDX_Data/Resources/` | Unity Technologies; distributed as part of a game built with Unity, under Unity's terms |
| Mono class libraries | `GameAssembly.dll`, `RTDX_Data/il2cpp_data/` | MIT and the licenses of the Mono project |
| WinPixEventRuntime | `WinPixEventRuntime.dll` | Microsoft, distributed with Unity's Windows player |
| Unity packages (uGUI, Timeline, TextMesh Pro, Cinemachine) | `GameAssembly.dll` | Unity Companion License |
| DOTween | `GameAssembly.dll` | Copyright Demigiant |
| RSG.Promise | `GameAssembly.dll` | MIT, Real Serious Games |
| Lua 5.1 (as the game links it, rebuilt for x64 inside the native layer) | `RTDX_Data/Plugins/rtdx_native.dll` | MIT, Lua.org, PUC-Rio |
| Python 3.13 and the libraries of its Windows build (OpenSSL, libffi, zlib, bzip2, xz, Tcl/Tk ...) | `mtdx_runtime/` | PSF License and the licenses listed in Python's `LICENSE.txt` |
| Python packages of the converter (UnityPy, texture2ddecoder, numpy, lz4 and their dependencies) | `mtdx_runtime/` | each package's own license (MIT, BSD-3-Clause, MIT-CMU, MPL-2.0 ...) |
| PyInstaller bootloader | `MTDX.exe` | GPL-2.0-or-later with the PyInstaller bootloader exception |

Every release carries the license texts of the Python side in `mtdx/licenses/`, collected by `release/make_release.py`
from the bundled packages' own metadata (`mtdx/licenses/index.txt` lists package, version and license).

MTDX contains no data of Pokémon Mystery Dungeon: Rescue Team DX. Pokémon is a trademark of Nintendo, Creatures and Game
Freak; MTDX is not affiliated with or endorsed by them, The Pokémon Company or Spike Chunsoft.
