"""Our Windows (Direct3D 11) shader objects, put in place of the Switch ones.

A Switch file holds NVN shader programs (platform 19) no PC GPU runs. Every Shader object of the game (84 in shader_pack.ab,
82 in resources.assets, 9 in sharedassets0.assets, 55 embedded in 49 other bundles) gets the data of OUR Windows build of the
same shader: compiled by Unity 2018.3.12f1 from the rewritten Pegasus shader sources of the RTDX PC rebuild, or Unity /
TextMesh Pro built-ins. The object keeps its Switch path ID, so every material still points at it.

The pack is made by release/make_shaderpack.py from a Windows player build. Entry key = (container, shader name): container =
the bundle file name ('shader_pack.ab', 'ui.ab', ...) or the player file name ('resources.assets', 'sharedassets0.assets') -
Unity compiles an embedded copy with the variants of ITS file's materials, so copies of one shader are not interchangeable.
References inside an entry (m_Dependencies: Fallback / UsePass shaders) are stored by meaning, not by ID, and resolved in the
target file:
  builtin  - a Unity built-in resource file (same path ID on every platform - checked for 2018.3.12f1: 35 + 72 objects);
  shaders  - a shader of shader_pack.ab, by name (its Switch path ID comes from the dump being converted);
  local    - an object of the same file, by type + name;
  file     - an object of another player file (resources.assets -> sharedassets0.assets: TextMesh Pro's Overlay shaders fall
             back to a shader stored there), by file + type + name, resolved in the dump's copy of that file.
The converter passes `ids` = {'shaders': {name: Switch path id in shader_pack.ab}, 'files': {file: {(type, name): path id}}}.
"""
import copy
import hashlib
import pickle
import zlib

from .unityfiles import ConvertError, add_external, type_hash, walk_pptrs

SHADERS_CAB = 'CAB-caafd6a11c9514e386d3454c9c3a0782'
SHADERS_IDENT = {'path': f'archive:/{SHADERS_CAB}/{SHADERS_CAB}', 'guid': '00' * 16, 'type': 0}
SHADERS_CONTAINER = 'shader_pack.ab'
PACK_VERSION = 1


class ShaderPack:
    def __init__(self, path):
        with open(path, 'rb') as f:
            raw = f.read()
        d = pickle.loads(zlib.decompress(raw) if raw[:1] == b'x' else raw)
        if d.get('version') != PACK_VERSION:
            raise ConvertError(f'shader pack {path}: version {d.get("version")}, this converter reads {PACK_VERSION}')
        self.entries = d['entries']           # {(container, name): entry}
        self.info = d.get('info', {})

    def get(self, container, name):
        e = self.entries.get((container, name))
        if e is None:
            raise ConvertError(f'no Windows build of shader "{name}" for {container} in the shader pack')
        return e

    def containers(self):
        return sorted({c for c, _ in self.entries})

    def container_digests(self):
        """{container: digest of every shader the pack holds for it} - a container whose digest changes between two
        releases must be converted again (its Shader objects carry the pack's data)."""
        per = {}
        for (container, name), e in sorted(self.entries.items()):
            per.setdefault(container, hashlib.sha1()).update(name.encode() + b'\0' + entry_digest(e).encode())
        return {c: h.hexdigest() for c, h in per.items()}


def entry_digest(e):
    if 'digest' not in e:
        e['digest'] = hashlib.sha1(pickle.dumps((e['tree'], e['refs'], e.get('type_hash')), protocol=4)).hexdigest()
    return e['digest']


def shader_name(obj):
    return obj.read_typetree()['m_ParsedForm']['m_Name']


def external_index_ci(sf, path):
    """1-based FileID of the external whose path equals `path` ignoring case (player files write the built-in resource
    files in lower case, bundles as 'Library/unity default resources'), or ends with /path; else None."""
    low = path.lower()
    for i, e in enumerate(sf.externals):
        p = e.path.lower()
        if p == low or p.endswith('/' + low):
            return i + 1
    return None


def object_names(sf):
    """{(type, name): path_id} of every named object of sf (Shader by parsed name), the first of a name kept."""
    m = {}
    for o in sorted(sf.objects.values(), key=lambda o: o.path_id):
        t = o.type.name
        try:
            n = shader_name(o) if t == 'Shader' else o.peek_name()
        except Exception:
            continue
        if n is not None:
            m.setdefault((t, n), o.path_id)
    return m


def local_names(sf, cache):
    """{(type, name): path_id} for the objects of sf a 'local' reference may name (Shader by parsed name, others by m_Name)."""
    key = id(sf)
    if key not in cache:
        m = {}
        for o in sf.objects.values():
            t = o.type.name
            if t == 'Shader':
                n = shader_name(o)
            else:
                try:
                    n = o.peek_name()
                except Exception:
                    continue
            if n is not None:
                m.setdefault((t, n), o.path_id)
        cache[key] = m
    return cache[key]


def apply(obj, sf, entry, ids, cache):
    """Writes entry's Windows shader into obj (a Shader object of sf), references resolved for sf."""
    switch_shader_ids = ids['shaders']
    want = type_hash(obj)
    if entry.get('type_hash') and want and entry['type_hash'] != want:
        raise ConvertError(f'shader {entry["name"]}: Shader type hash {want} in the file, {entry["type_hash"]} in the pack')
    tree = copy.deepcopy(entry['tree'])
    refs = entry['refs']

    def resolve(p):
        if p['m_FileID'] != -1:
            return None
        r = refs[p['m_PathID']]
        k = r['kind']
        if k == 'builtin':
            fid = external_index_ci(sf, r['ident']['path']) or add_external(sf, r['ident'])
            return {'m_FileID': fid, 'm_PathID': r['pid']}
        if k == 'local' and r['type'] == 'Shader' and local_names(sf, cache).get(('Shader', r['name'])) is None \
                and r['name'] in switch_shader_ids:
            k = 'shaders'           # a shader_pack.ab entry used by name in another file: its sibling lives in shader_pack.ab
        if k == 'shaders':
            if r['name'] not in switch_shader_ids:
                raise ConvertError(f'shader {entry["name"]}: dependency "{r["name"]}" is not in the dump\'s shader_pack.ab')
            if sf.name and sf.name.endswith(SHADERS_CAB):
                return {'m_FileID': 0, 'm_PathID': switch_shader_ids[r['name']]}
            fid = external_index_ci(sf, SHADERS_CAB) or add_external(sf, SHADERS_IDENT)
            return {'m_FileID': fid, 'm_PathID': switch_shader_ids[r['name']]}
        if k == 'file':
            pid = ids['files'].get(r['file'], {}).get((r['type'], r['name']))
            fid = external_index_ci(sf, r['file'])
            if pid is None or fid is None:
                raise ConvertError(f'shader {entry["name"]}: {r["type"]} "{r["name"]}" of {r["file"]} not found')
            return {'m_FileID': fid, 'm_PathID': pid}
        if k == 'local':
            pid = local_names(sf, cache).get((r['type'], r['name']))
            if pid is None:
                raise ConvertError(f'shader {entry["name"]}: local dependency {r["type"]} "{r["name"]}" not in {sf.name}')
            return {'m_FileID': 0, 'm_PathID': pid}
        raise ConvertError(f'shader {entry["name"]}: unknown reference kind {k}')

    walk_pptrs(tree, resolve)
    obj.save_typetree(tree)
