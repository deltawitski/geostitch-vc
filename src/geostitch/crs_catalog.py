from __future__ import annotations

from functools import lru_cache


PREFERRED_CRS = (
    "EPSG:4326",
    "EPSG:3857",
    "EPSG:32610",
    "EPSG:32611",
    "EPSG:26910",
    "EPSG:26911",
    "EPSG:2229",
)


@lru_cache(maxsize=1)
def compatible_crs_options() -> tuple[tuple[str, str], ...]:
    """Return non-deprecated, 2D geographic and projected EPSG coordinate systems."""
    from pyproj.database import query_crs_info
    from pyproj.enums import PJType

    infos = query_crs_info(
        auth_name="EPSG",
        pj_types=(PJType.GEOGRAPHIC_2D_CRS, PJType.PROJECTED_CRS),
        allow_deprecated=False,
    )
    names = {f"{info.auth_name}:{info.code}": info.name for info in infos}
    preferred = [(identifier, names.pop(identifier)) for identifier in PREFERRED_CRS if identifier in names]
    remainder = sorted(names.items(), key=lambda item: (item[1].casefold(), item[0]))
    return tuple((*preferred, *remainder))
