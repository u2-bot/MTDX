"""Problem texts a player can act on without help: what happened, then numbered steps of what to do (the window shows them
in its message box and log, the console after "PROBLEM:")."""
import os


def text(problem, steps=(), details=None):
    """'<problem>.\n\nWhat to do:\n  1. ...' (+ a details line for a report when the cause is technical)"""
    out = problem.strip()
    if out and out[-1] not in '.!?:)':
        out += '.'
    steps = [s for s in steps if s]
    if steps:
        out += '\n\nWhat to do:\n' + '\n'.join(f'  {i}. {s}' for i, s in enumerate(steps, 1))
    if details:
        out += f'\n\n(Details for a bug report: {details})'
    return out


def folder(path, root=None):
    """a path as the player sees it (relative to the MTDX folder when inside it)"""
    path = os.path.normpath(path)
    if root:
        try:
            rel = os.path.relpath(path, root)
            if not rel.startswith('..'):
                return rel
        except ValueError:
            pass
    return path
