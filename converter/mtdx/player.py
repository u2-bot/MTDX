"""The player's own data files that come from the dump.

level0 (the boot scene), sharedassets0.assets (+ .resS) and resources.assets (+ .resS, the Resources.Load objects): the
dump's files converted like a bundle (platform field, textures, shaders). Player files carry no type trees, so the Windows
player reads their MonoBehaviours with the layouts of our classes: proven equal for every object of the three files (822 of
825 parse byte-exact with both the shipped and our generated layouts; the other three are a generator limit on both sides or
debug-only objects). Their references into globalgamemanagers.assets (every MonoBehaviour's m_Script) use the Switch path
IDs: the release renumbers our script registry to them (release/make_player_base.py), so the files need no reference edits.
globalgamemanagers: ours (mtdx/base/) with the dump's ResourceManager object = the Resources.Load table, whose entries
point at the converted resources.assets / sharedassets0.assets by the Switch path IDs. Both files list their externals in
the same order.
"""
import os

import UnityPy

from . import bundle
from .unityfiles import ConvertError

# (file, has a .resS beside it)
PLAYER_FILES = (('level0', False), ('sharedassets0.assets', True), ('resources.assets', True))


def convert_player_file(src_data, out_data, name, pack, ids):
    """the dump's src_data/name (+ name.resS) -> out_data/name (+ .resS). -> stats"""
    src = os.path.join(src_data, name)
    res_name = name + '.resS'
    res_src = os.path.join(src_data, res_name)
    sources = {}
    if os.path.isfile(res_src):
        with open(res_src, 'rb') as f:
            sources[res_name] = f.read()
    sf = UnityPy.load(src).file
    stats = {}
    new_res = bundle.convert_serialized(sf, sources, res_name, name, pack, ids, stats)
    data = sf.save()
    write(os.path.join(out_data, name), data)
    if res_name in new_res:
        write(os.path.join(out_data, res_name), new_res[res_name])
    stats['bytes'] = len(data)
    return stats


def patch_globalgamemanagers(sw_file, base_file, out_data):
    """Our globalgamemanagers (base_file) with the ResourceManager object of the dump's globalgamemanagers sw_file ->
    out_data/globalgamemanagers."""
    sw = UnityPy.load(sw_file).file
    ours = UnityPy.load(base_file).file
    if [e.path for e in sw.externals] != [e.path for e in ours.externals]:
        raise ConvertError(f'globalgamemanagers externals differ: {[e.path for e in sw.externals]} vs '
                           f'{[e.path for e in ours.externals]}')
    rm_sw = [o for o in sw.objects.values() if o.type.name == 'ResourceManager']
    rm_ours = [o for o in ours.objects.values() if o.type.name == 'ResourceManager']
    if len(rm_sw) != 1 or len(rm_ours) != 1:
        raise ConvertError('expected one ResourceManager in each globalgamemanagers')
    rm_ours[0].set_raw_data(rm_sw[0].get_raw_data())
    data = ours.save()
    write(os.path.join(out_data, 'globalgamemanagers'), data)
    return {'bytes': len(data)}


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
