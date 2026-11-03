"""Build mtdx/shaders.pack (our Windows shader objects) from a Windows player build of the RTDX PC rebuild.

  py -3.13 -I release/make_shaderpack.py PLAYER_DATA PLAYER_BUNDLES SWITCH_DATA OUT.pack
PLAYER_DATA = the player's RTDX_Data (its resources.assets / sharedassets0.assets); PLAYER_BUNDLES = the folder of its
Windows bundles (<name>.ab, the same names as the dump's StreamingAssets/ab); SWITCH_DATA = the dump's romfs/Data, read only
to list which files hold Shader objects and to pick between duplicate copies - nothing of the dump goes into the pack.
Containers = every Switch file holding a Shader object (shader_pack.ab, 49 other bundles, resources.assets,
sharedassets0.assets). globalgamemanagers.assets is not one: the release ships our own (renumbered) copy of it.
Each entry = the Windows Shader typetree with its references stored by meaning (see converter/mtdx/shaderpack.py).
"""
import os
import pickle
import sys
import zlib

import UnityPy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'converter'))
from mtdx import shaderpack  # noqa: E402
from mtdx.unityfiles import serialized_files, type_hash, walk_pptrs  # noqa: E402

BUILTIN = ('resources/unity_builtin_extra', 'library/unity default resources')
PLAYER_FILES = ('resources.assets', 'sharedassets0.assets')
PLAYER_REFS = ('sharedassets0.assets', 'resources.assets', 'globalgamemanagers.assets')
_player_names = {}


def player_names(data_dir, fname):
    """{path id: (type, name)} of one of OUR player files (a reference from one player file into another)."""
    key = (data_dir, fname)
    if key not in _player_names:
        sf = UnityPy.load(os.path.join(data_dir, fname)).file
        _player_names[key] = {p: tn for tn, p in shaderpack.object_names(sf).items()}
    return _player_names[key]


def has_shader(path):
    env = UnityPy.load(path)
    return any(o.type.name == 'Shader' for _, sf in serialized_files(env) for o in sf.objects.values())


def names_of(sf):
    m = {}
    for o in sf.objects.values():
        if o.type.name == 'Shader':
            m[o.path_id] = ('Shader', shaderpack.shader_name(o))
        else:
            try:
                m[o.path_id] = (o.type.name, o.peek_name())
            except Exception:
                m[o.path_id] = (o.type.name, None)
    return m


def material_users(sf):
    """{shader path id (local): set of material names using it} for one serialized file."""
    out = {}
    for o in sf.objects.values():
        if o.type.name == 'Material':
            t = o.read_typetree()
            p = t['m_Shader']
            if p['m_FileID'] == 0 and p['m_PathID']:
                out.setdefault(p['m_PathID'], set()).add(t['m_Name'])
    return out


def pick_duplicates(sf, switch_path, container, log):
    """Our build can hold two Shader objects of one name (the rebuilt project keeps two copies of the installed shaders, and
    TextMesh Pro's); the Switch file has one. -> {path id to SKIP}: of each duplicate group keep the copy whose material
    users overlap most with the materials that use the Switch shader of that name (identical copies: the first)."""
    groups = {}
    for o in sf.objects.values():
        if o.type.name == 'Shader':
            groups.setdefault(shaderpack.shader_name(o), []).append(o)
    dups = {n: g for n, g in groups.items() if len(g) > 1}
    if not dups:
        return set()
    users = material_users(sf)
    sw_sf = serialized_files(UnityPy.load(switch_path))[0][1]
    sw_users = material_users(sw_sf)
    sw_by_name = {shaderpack.shader_name(o): o.path_id for o in sw_sf.objects.values() if o.type.name == 'Shader'}
    skip = set()
    stats = {'identical': 0, 'by_users': 0, 'first': 0}
    for n, g in sorted(dups.items()):
        want = sw_users.get(sw_by_name.get(n), set())
        trees = [pickle.dumps(o.read_typetree()) for o in g]
        if all(t == trees[0] for t in trees):
            best = g[0]
            stats['identical'] += 1
        else:
            score = {o.path_id: len(users.get(o.path_id, set()) & want) for o in g}
            best = max(g, key=lambda o: (score[o.path_id], -g.index(o)))
            if score[best.path_id] == 0:
                if n not in sw_by_name:
                    skip |= {o.path_id for o in g}      # the Switch file has no such shader: none is packed
                    continue
                # no material of either side tells: the copy with the most compiled programs (a superset of variants)
                best = max(g, key=lambda o: (len(o.read_typetree().get('compressedBlob', b'') or b''), -g.index(o)))
                stats['first'] += 1
                log(f'  {container}: {n}: {len(g)} different copies, no material tells - kept the largest ({best.path_id})')
            else:
                stats['by_users'] += 1
        skip |= {o.path_id for o in g if o is not best}
    log(f'  {container}: {len(dups)} duplicate names: {stats}')
    return skip


def pack_file(path, container, pc_shader_names, entries, switch_path, switch_names, log):
    env = UnityPy.load(path)
    for _, sf in serialized_files(env):
        names = None
        skip = pick_duplicates(sf, switch_path, container, log)
        for o in sf.objects.values():
            if o.type.name != 'Shader' or o.path_id in skip:
                continue
            tree = o.read_typetree()
            name = tree['m_ParsedForm']['m_Name']
            if name not in switch_names:
                continue                            # our build holds a shader the Switch file does not: nothing uses it
            refs = []

            def sym(p):
                fid, pid = p['m_FileID'], p['m_PathID']
                if pid == 0:
                    return None
                nonlocal names
                if fid == 0:
                    names = names or names_of(sf)
                    t, n = names[pid]
                    if n is None:
                        raise SystemExit(f'{container} {name}: local reference to an unnamed {t}')
                    refs.append({'kind': 'local', 'type': t, 'name': n})
                else:
                    e = sf.externals[fid - 1]
                    if e.path.lower() in BUILTIN:
                        g = e.guid.hex() if hasattr(e.guid, 'hex') else str(e.guid)
                        refs.append({'kind': 'builtin', 'ident': {'path': e.path, 'guid': g, 'type': getattr(e, 'type', 0)},
                                     'pid': pid})
                    elif e.path.endswith('/' + shaderpack.SHADERS_CAB):
                        refs.append({'kind': 'shaders', 'name': pc_shader_names[pid]})
                    elif e.path.lower() in PLAYER_REFS:
                        t, n = player_names(os.path.dirname(path), e.path.lower())[pid]
                        refs.append({'kind': 'file', 'file': e.path.lower(), 'type': t, 'name': n})
                    else:
                        raise SystemExit(f'{container} {name}: reference into {e.path} - not packable')
                return {'m_FileID': -1, 'm_PathID': len(refs) - 1}

            walk_pptrs(tree, sym)
            key = (container, name)
            if key in entries:
                raise SystemExit(f'{container}: two shaders named {name}')
            entries[key] = {'name': name, 'tree': tree, 'refs': refs, 'type_hash': type_hash(o)}
            shaderpack.entry_digest(entries[key])


def switch_shader_names(path):
    env = UnityPy.load(path)
    return {shaderpack.shader_name(o) for _, sf in serialized_files(env) for o in sf.objects.values() if o.type.name == 'Shader'}


def build(player_data, player_bundles, switch_data, out, log=print):
    sw_ab = os.path.join(switch_data, 'StreamingAssets', 'ab')
    containers = []
    for n in sorted(os.listdir(sw_ab)):
        if n.endswith('.ab') and has_shader(os.path.join(sw_ab, n)):
            containers.append((n, os.path.join(player_bundles, n), os.path.join(sw_ab, n)))
    for n in PLAYER_FILES:
        containers.append((n, os.path.join(player_data, n), os.path.join(switch_data, n)))
    log(f'containers with Shader objects: {len(containers)}')
    env = UnityPy.load(os.path.join(player_bundles, shaderpack.SHADERS_CONTAINER))
    pc_shader_names = {o.path_id: shaderpack.shader_name(o) for o in env.objects if o.type.name == 'Shader'}
    entries = {}
    want = 0
    for c, ours, sw in containers:
        if not os.path.isfile(ours):
            raise SystemExit(f'the player has no {c} ({ours})')
        names = switch_shader_names(sw)
        want += len(names)
        pack_file(ours, c, pc_shader_names, entries, sw, names, log)
        missing = sorted(n for n in names if (c, n) not in entries)
        if missing:
            raise SystemExit(f'{c}: no Windows build of {missing}')
    d = {'version': shaderpack.PACK_VERSION, 'entries': entries,
         'info': {'containers': [c for c, _, _ in containers]}}
    with open(out + '.part', 'wb') as f:
        f.write(zlib.compress(pickle.dumps(d, protocol=4), 9))
    os.replace(out + '.part', out)
    log(f'{len(entries)} shaders (the dump holds {want}) -> {out} ({os.path.getsize(out)} bytes)')
    return entries


def main():
    player_data, player_bundles, switch_data, out = sys.argv[1:5]
    build(player_data, player_bundles, switch_data, out)


if __name__ == '__main__':
    main()
