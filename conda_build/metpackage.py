from collections import defaultdict
from conda_build.metadata import MetaData


def create_metapackage(name, version, entry_points=(), build_string=None, build_number=0,
                       dependencies=(), home=None, license=None, summary=None,
                       anaconda_upload=None):
    # local import to avoid circular import, we provid create_metapackage in api.
    from conda_build.api import build
    d = defaultdict(dict)
    d['package']['name'] = name
    d['package']['version'] = version
    d['build']['number'] = build_number
    d['build']['entry_points'] = entry_points
    # MetaData does the auto stuff if the build string is None
    d['build']['string'] = build_string
    d['requirements']['run'] = dependencies
    d['about']['home'] = home
    d['about']['license'] = license
    d['about']['summary'] = summary
    d = dict(d)
    m = MetaData.fromdict(d)

    return build(m, anaconda_upload=anaconda_upload)
