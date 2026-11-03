"""release/bundles.txt = the bundles the converter converts: every StreamingAssets/ab/*.ab of the dump (2,670).

  py -3.13 -I release/make_bundle_list.py DUMP
Needed only if the game's bundle set changes (another game version).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ab = os.path.join(os.path.abspath(sys.argv[1]), 'romfs', 'Data', 'StreamingAssets', 'ab')
    names = sorted(n for n in os.listdir(ab) if n.endswith('.ab'))
    with open(os.path.join(HERE, 'bundles.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(names) + '\n')
    print(f'{len(names)} bundles -> {os.path.join(HERE, "bundles.txt")}')


if __name__ == '__main__':
    main()
