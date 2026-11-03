# Developing MTDX

## Layout

| Path | What |
|---|---|
| `converter/mtdx/` | The converter that ships as `MTDX.exe` (Python 3.13 + UnityPy) |
| `converter/MTDX.py` | Entry script (PyInstaller, or `py -3.13 -I converter/MTDX.py ...` from the source tree) |
| `release/` | Tools that make a release from a Windows player build, and the developer checks |
| `release/bundles.txt` | The 2,670 bundles the converter converts (every `StreamingAssets/ab/*.ab` of the dump) |

Pinned inputs: `converter/requirements.txt` (runtime), `release/requirements-build.txt` (release tooling).

## How a dump becomes game data

Measured on the Rescue Team DX dump (version 1.0.2: the base game with its 1.0.2 update; identified by the build ID of
`exefs/main`, 3AB632DEE82D5944, which cheat and mod databases list as 1.0.2 - the game itself calls it build 1500 in
`StreamingAssets/data/revisionDataLocal.xml`) against the RTDX PC rebuild's Windows player built from it:

- **Bundles** (`bundle.py`). Switch and Windows bundles share the archive format (UnityFS, Unity 2018.3.12f1), the
  serialized-file version (17) and type trees (all 2,670 bundles carry them); bundle names and internal file names
  (`CAB-...`) are the same, so references between bundles need no change. The converter changes only:
  - the target-platform field of each serialized file (38 Switch, 19 StandaloneWindows64);
  - texture data (`textures.py`): ASTC (2,531 textures) is decoded level by level into RGBA32, exactly the texels the
    Switch GPU samples; DXT1 / DXT5 / RGB24 / RGBA32 / Alpha8 are copied byte for byte. No Switch texture has a platform
    blob: Unity 2018.3's Switch build stores them in the ordinary linear layout. Nothing is re-encoded, so the output is the
    same on every CPU;
  - Shader objects: each gets the data of our Direct3D 11 build of the same shader from `mtdx/shaders.pack`, keeping its
    Switch path ID;
  - a new `.resS` per serialized file (converted texture data plus the untouched mesh streams).
  Every other object keeps its bytes. Audio (`.resource` entries: FSB5 Vorbis, mode 15, the header Unity writes for
  Windows too) is carried byte for byte. Output is LZ4 chunk-compressed like Unity's own bundles.
- **The player loads them with the game's own loader.** `RtdxPc/PcLayout.cs` of the rebuild: a player folder without
  `StreamingAssets/abpc/bundle_map.tsv` (the rebuild's Windows bundles) but with `StreamingAssets/ab/dependency.txt` runs
  the shipped `ResourceCache.AssetBundleResource` path (dependency.txt, `LoadFromMemory` through `nn::fs`, whose PC layer
  reads host paths), exactly as the console does. The play-test archive of every save is off there.
- **Player files** (`player.py`): `level0`, `sharedassets0.assets(+.resS)`, `resources.assets(+.resS)` are the dump's,
  converted like a bundle. They carry no type trees, so the player reads their MonoBehaviours with our classes' layouts:
  proven equal (822 of the 825 objects parse byte-exact with both the shipped layout - generated from Il2CppDumper's
  DummyDll - and ours; the other three fail identically on both sides or are debug-only objects whose classes the rebuild
  does not have: `DebugDatabase`, `TopMenu`).
- **globalgamemanagers**: our copy (`mtdx/base/`) with the dump's ResourceManager (the `Resources.Load` table) put in.
- **The native data image** (`native.py`): `rtdx_native.dll` is the game's native code rebuilt for x64; its data is the
  console executable's `.rodata` / `.data` at the addresses the code was translated against. `exefs/main` and `exefs/sdk`
  (NSO0, LZ4 segments) are laid out flat and turned into `StreamingAssets/rtdx/rtdx_image.bin` - byte-identical to the
  image the player was tested with (its sha1 is in `mtdx/release.json`; the converter refuses any other result).
- **Copied as they are**: `StreamingAssets/ab/dependency.txt`, `StreamingAssets/data/`, `StreamingAssets/native_data/`
  (the data tables, Lua scripts, messages the game reads).

What the release side prepares so the dump's files fit our player (`release/make_player_base.py`):

- **Script registry.** The dump's player files point into `globalgamemanagers.assets` by Switch path IDs (every
  MonoBehaviour's `m_Script`, the files' script-type tables, ResourceManager dependencies). Our Mono build numbered its
  registry its own way (1,945 objects against the Switch build's 1,272). Our registry is renumbered: every MonoScript whose
  class (namespace + name + assembly) the Switch registry holds takes the Switch path ID (Unity writes two objects for 77
  package classes in both builds: paired in order), Sprites-Default and its shaders likewise; the rest go above the Switch
  range. MonoManager, GraphicsSettings and ResourceManager follow the new IDs. Every reference of the dump's player files
  into the registry then resolves to the right class, except the two debug-only classes above.
- **ScriptMapper** (Shader.Find's table) is pointed at the Switch resources.assets / sharedassets0.assets shaders by name.
- **Shader pack** (`release/make_shaderpack.py`): the Windows Shader objects of our player's bundles and player files,
  per (container, shader name), 228 entries = every Shader object of the dump except the two in globalgamemanagers.assets
  (ours ship). References inside them are stored by meaning: Unity built-in files (same path IDs on both platforms -
  checked: 35 + 72 objects), shaders of `shader_pack.ab` by name, objects of the same file, or of another player file
  (TextMesh Pro's Overlay shaders in resources.assets fall back to a shader of sharedassets0.assets).

## Updates (a new release over the player's folder)

At the end of each conversion `mtdx/state/config.json` records a fingerprint: the hashes of `mtdx/base/globalgamemanagers`
and `RTDX_Data/globalgamemanagers.assets`, a digest per container of `mtdx/shaders.pack` and the native image's sha1. At
every start `install.status()` compares it with the files now in the folder; the window then converts again with the dump
of the last conversion. Bundles are made again only when their dump file (size + time) or their container's shader digest
changed (`done_v<N>.log`); the player files, globalgamemanagers and the copies are cheap and are always made again. Rule:
bump `CONVERT_VERSION` in `converter/mtdx/__init__.py` whenever a change alters the bytes the converter writes for bundles.

## Making a release

Input: a Windows player build of the RTDX PC rebuild (`RTDX.exe` + `RTDX_Data`, with `RtdxPc/PcLayout.cs`), its Windows
bundles (the folder of `<name>.ab` files the rebuild's player build makes) and the dump on the build machine. The dump is used
for IDs, names and the audit; nothing of it is copied into the release.

```
py -3.13 -m pip install -r converter/requirements.txt -r release/requirements-build.txt
py -3.13 -I release/build_exe.py                         # -> build/dist/MTDX (MTDX.exe + mtdx_runtime/)
py -3.13 -I release/make_release.py PLAYER_DIR PLAYER_BUNDLES DUMP OUT_DIR --exe build/dist/MTDX
py -3.13 -I release/zip_release.py OUT_DIR               # -> MTDX-<version>-win64.zip
```

`make_release.py` copies the player without its game data, strips the debug sections of `rtdx_native.dll` (MinGW
`objcopy --strip-debug`; every loadable section is checked to keep its bytes), cuts the build machine's `.pdb` paths out of
our own assemblies, builds the player base and the shader pack, writes `mtdx/release.json`, copies the frozen converter and
the license texts of every package in its runtime (`mtdx/licenses/`), and runs the **audit**: it fails the release when any
file has the bytes of a dump file, names a build-machine folder, or when a serialized file of ours holds textures, meshes,
sounds, text, scripts' data or other game objects.

## Checks before a release

1. Convert the dump into a copy of the release folder: `MTDX.exe convert DUMP` (about 20 s with 16 workers).
2. `release/verify_bundle.py DUMP/romfs/Data/StreamingAssets/ab OUT/RTDX_Data/StreamingAssets/ab --all` and
   `release/verify_bundle.py DUMP/romfs/Data OUT/RTDX_Data level0 sharedassets0.assets resources.assets`: every object
   against its Switch source (decoded textures equal, mesh streams equal, audio equal, every other object
   byte-identical).
3. `release/compare_trees.py` against a second conversion (other worker count, source tree vs MTDX.exe): 0 differing files.
4. In game (a scripted run: an isolated user profile holding a copy of a save or none, input by game tick through the
   player's test driver, screenshots): boot to the title, a new game from no save, a dungeon from a save; output_log.txt without new errors.

## Measurements (version 1.0.2)

- Bundles: 2.0 GB in, about 2.2 GB out (2,498 textures decoded, 5,125 copied, 137 shaders replaced; 361,978 objects).
- Converted folder: 2.4 GB.
- Time: about 20 s with 16 workers on a 32-thread desktop (17 s of it the bundles).
