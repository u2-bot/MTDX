"""Dump -> playable Windows game, in place in the MTDX folder.

Layout of an MTDX folder: RTDX.exe + RTDX_Data/ (our player, no game data), MTDX.exe (this converter) + mtdx_runtime/, and
mtdx/ (its data: shaders.pack, base/globalgamemanagers, bundles.txt, release.json, state/). The converter writes the game
data into RTDX_Data:
  StreamingAssets/ab/<2,670 bundles>     converted (bundle.py), in parallel, resumable
  StreamingAssets/ab/dependency.txt, StreamingAssets/data/, StreamingAssets/native_data/   copied byte for byte (the game
                                         reads them as they are)
  StreamingAssets/rtdx/rtdx_image.bin    the native code's data image (native.py), from exefs/main + exefs/sdk
  level0, sharedassets0.assets(+.resS), resources.assets(+.resS)   the dump's player files, converted (player.py)
  globalgamemanagers                     ours (mtdx/base) + the dump's ResourceManager
State: mtdx/state/done_v<CONVERT_VERSION>.log lists each finished bundle with the source it was made from (the dump file's
size + time) and the shader-pack digest of its container; a bundle is made again when either changes. Outputs are written to
a .part file and renamed when complete. state/config.json keeps the dump and a fingerprint of what the data was made against
(player base, shader pack, native image); status() compares it with the folder at every start.
"""
import concurrent.futures as cf
import hashlib
import json
import os
import shutil
import time

from . import CONVERT_VERSION, advice
from .unityfiles import ConvertError

# Rescue Team DX version 1.0.2 (the base game with its 1.0.2 update; exefs/main build ID 3AB632DEE82D5944): files a correct
# dump has byte-identical (path, sha1, size). The player's
# native code was rebuilt from exactly this main + sdk; its script registry and globalgamemanagers fit this romfs.
FINGERPRINTS = [
    ('exefs/main', 'eb5c579d753417728321aa968043ecaf7736f9e6', 34778643),
    ('exefs/sdk', 'ce34274ad9b415fc556b081d260682615a915a3c', 5309803),
    ('romfs/Data/globalgamemanagers', '3b2c32e1439a66df2c8023cdc282a4d5ad12586e', 140512),
]
MARK = ('romfs', 'Data', 'StreamingAssets', 'ab', 'dependency.txt')
COPY_TREES = ['StreamingAssets/data', 'StreamingAssets/native_data']
COPY_FILES = ['StreamingAssets/ab/dependency.txt']
PLAYER_INPUTS = ['level0', 'sharedassets0.assets', 'sharedassets0.assets.resS', 'resources.assets', 'resources.assets.resS',
                 'globalgamemanagers']
# measured on the whole dump: 2.0 GB of Switch bundles -> about 2.9 GB (ASTC decoded to RGBA32, LZ4)
BUNDLE_GROWTH = 1.6


class DumpError(Exception):
    pass


def is_mtdx_folder(path):
    return os.path.isfile(os.path.join(path, 'RTDX.exe')) and os.path.isdir(os.path.join(path, 'mtdx'))


def find_dump(selected):
    """-> the dump folder (the one holding romfs/ and exefs/) under / around what the user picked (the dump folder, romfs,
    romfs/Data, exefs, a title-id folder above them ...). Raises DumpError with a message a player can act on."""
    sel = os.path.abspath(selected)
    if not os.path.isdir(sel):
        raise DumpError(advice.text(f'The folder {sel} does not exist (it was moved, renamed, or is on a drive that is not '
                                    f'connected)', ['Press Browse... and pick the folder of your dump where it is now']))
    if is_mtdx_folder(sel):
        raise DumpError(advice.text(f'{sel} is the MTDX folder, not your game dump',
                                    ['Press Browse... and pick the folder you extracted your Rescue Team DX dump to (the '
                                     'one that holds "romfs" and "exefs")']))
    cands = [sel, os.path.dirname(sel), os.path.dirname(os.path.dirname(sel))]
    for root, dirs, _ in os.walk(sel):
        depth = root[len(sel):].count(os.sep)
        if depth >= 3:
            dirs[:] = []
            continue
        for d in dirs:
            cands.append(os.path.join(root, d))
    romfs_only = None
    for c in cands:
        if os.path.isfile(os.path.join(c, *MARK)):
            if os.path.isfile(os.path.join(c, 'exefs', 'main')):
                return c
            romfs_only = romfs_only or c
    if romfs_only:
        raise DumpError(advice.text(
            f'{romfs_only} holds the game\'s romfs but no exefs folder next to it',
            ['MTDX needs both parts of the dump: "romfs" (the game data) and "exefs" (the game program - its data tables '
             'are part of the game too). Extract the exefs with your dumping tool into the same folder, so that the folder '
             'holds "romfs" and "exefs" side by side',
             'Then press Browse... and pick that folder']))
    raise DumpError(advice.text(
        f'{sel} does not hold the game files of Pokemon Mystery Dungeon: Rescue Team DX',
        ['Pick the folder you extracted your own Rescue Team DX dump to: it holds a folder "romfs" (with "Data" inside, and '
         '"Data" holds "StreamingAssets" and "globalgamemanagers") and a folder "exefs" (with "main" and "sdk" inside)',
         'If you only have the game as one file (.nsp / .xci), extract its romfs AND exefs first with your dumping tool']))


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_dump(dump, bundles):
    """-> list of problems, each a text a player can act on (empty = a complete dump of version 1.0.2)."""
    redump = ('Extract your dump again into a NEW, empty folder (romfs and exefs), then pick that folder')
    for rel, want, size in FINGERPRINTS:
        p = os.path.join(dump, *rel.split('/'))
        if not os.path.isfile(p):
            return [advice.text(f'Your dump has no {rel}: it is incomplete', [redump])]
        if os.path.getsize(p) != size or sha1(p) != want:
            return [advice.text(
                f'Your dump\'s {rel} is not the one of Rescue Team DX version 1.0.2, the version MTDX supports: it is an '
                f'older version of the game (1.0.0 or 1.0.1: without the 1.0.2 update), or a dump with mod files copied into '
                f'it', ['Dump the game together with its 1.0.2 update: extract the base game and the update together (the '
                        'update\'s files over the base game\'s), without mods', redump])]
    data = os.path.join(dump, 'romfs', 'Data')
    bad = []
    ab = os.path.join(data, 'StreamingAssets', 'ab')
    missing = [b for b in bundles if not os.path.isfile(os.path.join(ab, b))]
    if missing:
        bad.append(advice.text(f'{len(missing)} of the {len(bundles)} game files are missing from your dump (for example '
                               f'romfs/Data/StreamingAssets/ab/{missing[0]}): the extraction stopped before the end, or '
                               f'files were deleted', [redump, 'Check that the drive has room for the whole game '
                                                                '(about 2.2 GB)']))
    for rel in PLAYER_INPUTS + COPY_FILES:
        if not os.path.isfile(os.path.join(data, *rel.split('/'))):
            bad.append(advice.text(f'Your dump has no romfs/Data/{rel}: it is incomplete', [redump]))
    for t in COPY_TREES:
        if not os.path.isdir(os.path.join(data, *t.split('/'))):
            bad.append(advice.text(f'Your dump has no romfs/Data/{t} folder: it is incomplete', [redump]))
    return bad


class Paths:
    def __init__(self, root):
        self.root = os.path.abspath(root)                       # the MTDX folder (holds RTDX.exe)
        self.out = os.path.join(self.root, 'RTDX_Data')
        self.sa = os.path.join(self.out, 'StreamingAssets')
        self.ab = os.path.join(self.sa, 'ab')
        self.data = os.path.join(self.root, 'mtdx')
        self.pack = os.path.join(self.data, 'shaders.pack')
        self.bundles = os.path.join(self.data, 'bundles.txt')
        self.base_ggm = os.path.join(self.data, 'base', 'globalgamemanagers')
        self.state = os.path.join(self.data, 'state')
        self.done_log = os.path.join(self.state, f'done_v{CONVERT_VERSION}.log')
        self.config = os.path.join(self.state, 'config.json')
        self.image = os.path.join(self.sa, 'rtdx', 'rtdx_image.bin')
        self.exe = os.path.join(self.root, 'RTDX.exe')

    def bundle_list(self):
        return [l.strip() for l in open(self.bundles, encoding='utf-8') if l.strip()]


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.part', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(path + '.part', path)


def load_config(p):
    return _load_json(p.config, {})


def save_config(p, cfg):
    _save_json(p.config, cfg)


def release_info(p):
    """mtdx/release.json: MTDX version, the native image's sha1, the player build ..."""
    return _load_json(os.path.join(p.data, 'release.json'), {})


def pack_digests(p):
    """{container: digest} of the shader pack, cached in state/ by the pack file's size + mtime."""
    st = os.stat(p.pack)
    key = f'{st.st_size}|{int(st.st_mtime)}'
    cache = os.path.join(p.state, 'pack_digests.json')
    d = _load_json(cache, {})
    if d.get('key') == key:
        return d['digests']
    from .shaderpack import ShaderPack
    digests = ShaderPack(p.pack).container_digests()
    _save_json(cache, {'key': key, 'digests': digests})
    return digests


def fingerprint(p):
    """What the converted data was made against: player base globalgamemanagers + registry, shader pack, native image."""
    reg = os.path.join(p.out, 'globalgamemanagers.assets')
    return {'base_ggm': sha1(p.base_ggm), 'registry': sha1(reg) if os.path.isfile(reg) else None,
            'pack': pack_digests(p), 'image': release_info(p).get('image_sha1')}


def player_outputs():
    return ['level0', 'sharedassets0.assets', 'sharedassets0.assets.resS', 'resources.assets', 'resources.assets.resS',
            'globalgamemanagers']


def status(p):
    """-> {'state': 'ready' | 'refresh' | 'convert', 'reason': text}. convert = no complete conversion for this
    CONVERT_VERSION; refresh = something changed since the conversion (a release update of our player base or shaders,
    missing files): a conversion with the same dump makes only the affected files again."""
    cfg = load_config(p)
    if cfg.get('complete_version') != CONVERT_VERSION:
        if cfg.get('complete_version') is not None:
            return {'state': 'convert', 'reason': 'this MTDX version converts the game data in a new way'}
        return {'state': 'convert', 'reason': 'not converted yet'}
    old, now = cfg.get('fingerprint') or {}, fingerprint(p)
    reasons = []
    stale = sorted(c for c, d in now['pack'].items() if (old.get('pack') or {}).get(c) != d)
    if stale:
        reasons.append(f'new shaders for {len(stale)} files')
    if old.get('base_ggm') != now['base_ggm'] or old.get('registry') != now['registry']:
        reasons.append('a new player')
    if old.get('image') != now['image']:
        reasons.append('a new native image')
    missing = [x for x in player_outputs() if not os.path.isfile(os.path.join(p.out, x))]
    if missing:
        reasons.append('missing ' + ', '.join(missing))
    if not os.path.isfile(p.image):
        reasons.append('missing the native image')
    done = done_map(p)
    gone = sum(1 for rel in done if not os.path.isfile(os.path.join(p.ab, rel)))
    if gone:
        reasons.append(f'{gone} converted bundles missing')
    if reasons:
        return {'state': 'refresh', 'reason': '; '.join(reasons)}
    return {'state': 'ready', 'reason': ''}


def done_map(p):
    """{bundle: (source key, container digest)} of the finished bundles (last line wins)."""
    out = {}
    try:
        with open(p.done_log, encoding='utf-8') as f:
            for line in f:
                parts = line.rstrip('\n').split('\t')
                if parts[0] and len(parts) >= 3:
                    out[parts[0]] = (parts[1], parts[2])
    except OSError:
        pass
    return out


def write_done_map(p, done):
    os.makedirs(p.state, exist_ok=True)
    with open(p.done_log + '.part', 'w', encoding='utf-8', newline='\n') as f:
        for rel in sorted(done):
            f.write(f'{rel}\t{done[rel][0]}\t{done[rel][1]}\n')
    os.replace(p.done_log + '.part', p.done_log)


def source_key(path):
    st = os.stat(path)
    return f'{st.st_size}:{int(st.st_mtime)}'


# --- worker process ---------------------------------------------------------------------------------------------------
_W = {}


def _init(pack_path, ids):
    from .shaderpack import ShaderPack
    _W['pack'] = ShaderPack(pack_path)
    _W['ids'] = ids


def _job(args):
    src, dst, rel = args
    from . import bundle
    t0 = time.time()
    try:
        st = bundle.convert_bundle(src, dst, rel, _W['pack'], _W['ids'])
        return rel, st, None, time.time() - t0
    except Exception as e:      # reported, the run continues and fails at the end
        import traceback
        return rel, None, f'{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}', time.time() - t0


def switch_ids(data):
    """{'shaders': {name: path id} of the dump's shader_pack.ab, 'files': {player file: {(type, name): path id}}}"""
    import UnityPy
    from .shaderpack import SHADERS_CONTAINER, object_names, shader_name
    env = UnityPy.load(os.path.join(data, 'StreamingAssets', 'ab', SHADERS_CONTAINER))
    shaders = {}
    for o in sorted(env.objects, key=lambda o: o.path_id):
        if o.type.name == 'Shader':
            shaders.setdefault(shader_name(o), o.path_id)
    files = {}
    for f in ('sharedassets0.assets', 'resources.assets', 'globalgamemanagers.assets'):
        files[f] = object_names(UnityPy.load(os.path.join(data, f)).file)
    return {'shaders': shaders, 'files': files}


def memory_free():
    """-> (free RAM, free commit) in bytes, or None"""
    try:
        import ctypes

        class M(ctypes.Structure):
            _fields_ = [('l', ctypes.c_ulong), ('ml', ctypes.c_ulong)] + [(k, ctypes.c_ulonglong) for k in
                                                                           ('t', 'a', 'tp', 'ap', 'tv', 'av', 'x')]
        m = M()
        m.l = ctypes.sizeof(M)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None
        return m.a, m.ap
    except Exception:
        return None


def default_workers():
    n = os.cpu_count() or 2
    mem = memory_free()
    # one worker peaks well below 1 GB (the biggest bundle, ui.ab: 59 MB in, 86 MB out)
    ram_workers = int(min(mem) / 2 ** 30 / 1.0) if mem else n
    return max(1, min(n - 1 if n > 2 else n, ram_workers, 16))


def _gib(n):
    return f'{n / 2 ** 30:.1f} GB'


def run_pool(fn, jobs, workers, log, initializer=None, initargs=()):
    """yields fn(job) for every job, run in up to `workers` processes, in the order they finish. A worker that dies (Windows
    out of memory, an antivirus ending it) breaks the whole pool: what is not done yet runs again in a new pool with half
    the workers, down to one; a lone worker that dies three pools in a row before finishing anything = ConvertError."""
    from concurrent.futures.process import BrokenProcessPool
    left = list(jobs)
    idle = 0
    while left:
        n = max(1, min(workers, len(left)))
        finished = set()
        broken = False
        ex = cf.ProcessPoolExecutor(max_workers=n, initializer=initializer, initargs=initargs)
        try:
            futs = {ex.submit(fn, j): i for i, j in enumerate(left)}
            for f in cf.as_completed(futs):
                if isinstance(f.exception(), BrokenProcessPool):
                    broken = True
                    continue
                finished.add(futs[f])
                yield f.result()
        finally:
            ex.shutdown(wait=True, cancel_futures=True)
        left = [j for i, j in enumerate(left) if i not in finished]
        if not broken:
            continue
        mem = memory_free()
        why = f'free RAM {_gib(mem[0])}, free commit {_gib(mem[1])}' if mem else 'no memory figures'
        if n > 1:
            workers = max(1, n // 2)
            log(f'a helper process stopped ({why}): {len(left)} files go on with {workers} worker(s)')
            continue
        idle = 0 if finished else idle + 1
        if idle >= 3:
            raise ConvertError(advice.text(
                'MTDX\'s helper processes keep stopping: Windows ended them (it ran out of memory) or another program did '
                '(an antivirus)',
                ['Close other programs and press Convert again: MTDX goes on where it stopped',
                 'If an antivirus runs on this PC, add the MTDX folder to its exclusions, then press Convert again'],
                details=why))
        log(f'a helper process stopped ({why}): trying again')


def place(src, dst):
    """copies src -> dst through dst.part when size or time differ; -> True when it copied"""
    if os.path.isfile(dst):
        a, b = os.stat(src), os.stat(dst)
        if a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime):
            return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + '.part'
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    return True


def copy_data(data, p, log):
    """the folders and files the game reads as they are -> RTDX_Data/StreamingAssets; files no longer in the dump removed"""
    n = copied = 0
    for t in COPY_TREES:
        src_root = os.path.join(data, *t.split('/'))
        dst_root = os.path.join(p.out, *t.split('/'))
        want = set()
        for d, _, fs in os.walk(src_root):
            for f in fs:
                rel = os.path.relpath(os.path.join(d, f), src_root)
                want.add(os.path.normcase(rel))
                copied += place(os.path.join(d, f), os.path.join(dst_root, rel))
                n += 1
        for d, _, fs in os.walk(dst_root):
            for f in fs:
                rel = os.path.relpath(os.path.join(d, f), dst_root)
                if os.path.normcase(rel) not in want:
                    os.remove(os.path.join(d, f))
    for rel in COPY_FILES:
        copied += place(os.path.join(data, *rel.split('/')), os.path.join(p.out, *rel.split('/')))
        n += 1
    log(f'game data files: {n} ({copied} copied, the rest unchanged)')


def check_space(p, data, todo, log):
    try:
        free = shutil.disk_usage(p.root).free
    except OSError:
        return
    need = int(sum(os.path.getsize(os.path.join(data, 'StreamingAssets', 'ab', r)) for r in todo) * BUNDLE_GROWTH) + (200 << 20)
    if free < need:
        raise ConvertError(advice.text(
            f'The drive of {p.root} has {_gib(free)} free, the game data needs about {_gib(need)} more',
            ['Free some space on that drive (or move the whole MTDX folder to a drive with more room), then press Convert '
             'again: MTDX goes on where it stopped']))


def convert(p, dump, workers=None, log=print, progress=None):
    """Converts the dump into the MTDX folder. progress(stage, done, total). Raises DumpError / ConvertError."""
    t_start = time.time()
    dump = find_dump(dump)
    bundles = p.bundle_list()
    problems = verify_dump(dump, bundles)
    if problems:
        raise DumpError('\n\n'.join(problems))
    data = os.path.join(dump, 'romfs', 'Data')
    cfg = load_config(p)
    cfg['dump'] = dump
    cfg.pop('complete_version', None)
    save_config(p, cfg)
    log(f'dump: {dump}')

    # 1. the native data image (seconds; first, so a wrong executable stops before anything long)
    from . import native
    want = release_info(p).get('image_sha1')
    if not (os.path.isfile(p.image) and want and sha1(p.image) == want):
        if progress:
            progress('native data', 0, 1)
        img = native.make(os.path.join(dump, 'exefs', 'main'), os.path.join(dump, 'exefs', 'sdk'), want)
        from .player import write
        write(p.image, img)
        log(f'native data image: {len(img) / 2 ** 20:.1f} MB')
    if progress:
        progress('native data', 1, 1)

    # 2. bundles
    digests = pack_digests(p)
    done = done_map(p)
    jobs, keys = [], {}
    for rel in bundles:
        src = os.path.join(data, 'StreamingAssets', 'ab', rel)
        k = (source_key(src), digests.get(rel, '-'))
        keys[rel] = k
        if done.get(rel) == k and os.path.isfile(os.path.join(p.ab, rel)):
            continue
        jobs.append((src, os.path.join(p.ab, rel), rel))
    # converted bundles the release no longer lists
    listed = set(bundles)
    for rel in list(done):
        if rel not in listed:
            done.pop(rel)
            try:
                os.remove(os.path.join(p.ab, rel))
            except OSError:
                pass
    check_space(p, data, [j[2] for j in jobs], log)
    jobs.sort(key=lambda j: -os.path.getsize(j[0]))            # biggest first: the pool stays busy to the end
    total = len(bundles)
    have = total - len(jobs)
    errors = []
    if jobs:
        n = workers or default_workers()
        log(f'bundles: {len(jobs)} to convert ({have} already done), {n} worker(s)')
        ids = switch_ids(data)
        t0 = time.time()
        last_save = time.time()
        stats = {}
        for rel, st, err, dt in run_pool(_job, jobs, n, log, _init, (p.pack, ids)):
            if err:
                errors.append((rel, err))
                log(f'FAILED {rel}: {err.splitlines()[0]}')
            else:
                done[rel] = keys[rel]
                have += 1
                for k, v in st.items():
                    if isinstance(v, int):
                        stats[k] = stats.get(k, 0) + v
            if progress:
                progress('bundles', have, total)
            if time.time() - last_save > 5:
                write_done_map(p, done)
                last_save = time.time()
        write_done_map(p, done)
        log(f'bundles: {len(jobs) - len(errors)} converted in {time.time() - t0:.0f} s '
            f'({stats.get("decoded", 0)} textures decoded, {stats.get("copy", 0)} copied, {stats.get("shaders", 0)} shaders)')
    else:
        write_done_map(p, done)
        if progress:
            progress('bundles', total, total)
    if errors:
        raise ConvertError(advice.text(
            f'{len(errors)} game files could not be converted (for example {errors[0][0]})',
            ['Press Convert again: MTDX tries only those files again',
             'If they fail again, extract your dump again into a new folder and pick that one',
             'If that does not help either, report it with the lines of the log in the MTDX window'],
            details=errors[0][1].splitlines()[0]))

    # 3. player files + globalgamemanagers
    from . import player
    from .shaderpack import ShaderPack
    pack = ShaderPack(p.pack)
    ids = switch_ids(data)
    for i, (name, _) in enumerate(player.PLAYER_FILES):
        if progress:
            progress('player files', i, len(player.PLAYER_FILES) + 1)
        st = player.convert_player_file(data, p.out, name, pack, ids)
        log(f'{name}: {st.get("bytes", 0) / 2 ** 20:.1f} MB')
    player.patch_globalgamemanagers(os.path.join(data, 'globalgamemanagers'), p.base_ggm, p.out)
    if progress:
        progress('player files', len(player.PLAYER_FILES) + 1, len(player.PLAYER_FILES) + 1)

    # 4. data the game reads as it is
    copy_data(data, p, log)

    cfg = load_config(p)
    cfg.update({'dump': dump, 'complete_version': CONVERT_VERSION, 'fingerprint': fingerprint(p),
                'converted': time.strftime('%Y-%m-%d %H:%M:%S')})
    save_config(p, cfg)
    log(f'done in {time.time() - t_start:.0f} s: the game is ready - press Play (or start RTDX.exe)')
    return True
