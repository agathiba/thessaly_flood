"""Sentinel-1 change-detection flood mapping helpers (Google Earth Engine).

Call ee.Initialize(...) before using any function in this module.
"""
import ee

# Thessaly plain: Larissa, Karditsa, Trikala and the Karla basin
AOI_BOUNDS = [21.60, 39.20, 22.90, 39.85]
PRE_START, PRE_END = "2023-08-15", "2023-09-04"   # before Storm Daniel (4-7 Sep 2023)


def get_aoi():
    return ee.Geometry.Rectangle(AOI_BOUNDS)


def s1_vv(region):
    """Sentinel-1 GRD, IW mode, VV polarisation over the region."""
    return (ee.ImageCollection("COPERNICUS/S1_GRD")
            .filterBounds(region)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .select("VV"))


def flood_map(region, post_start, post_end, pre_start=PRE_START, pre_end=PRE_END,
              diff_thr=-3, post_thr=-15, smooth_m=50, max_slope=5, min_pixels=8):
    """Flood mask from one Sentinel-1 pass, compared with the same relative orbit before the event.

    Returns (flood, footprint):
      flood     – 1 = flooded, 0 = not flooded (inside the image footprint)
      footprint – 1 where the post-event image has data
    """
    s1 = s1_vv(region)
    post_c = s1.filterDate(post_start, post_end)
    orbit = post_c.first().get("relativeOrbitNumber_start")
    post_c = post_c.filter(ee.Filter.eq("relativeOrbitNumber_start", orbit))
    pre_c = s1.filterDate(pre_start, pre_end).filter(ee.Filter.eq("relativeOrbitNumber_start", orbit))

    pre = pre_c.median().clip(region).focalMedian(smooth_m, "circle", "meters")
    post = post_c.mosaic().clip(region).focalMedian(smooth_m, "circle", "meters")
    diff = post.subtract(pre)

    permanent_water = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gt(80).unmask(0)
    slope = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003"))

    flood = (diff.lt(diff_thr).And(post.lt(post_thr))
             .And(permanent_water.Not())
             .And(slope.lt(max_slope)))
    connected = flood.selfMask().connectedPixelCount(25)
    flood = flood.And(connected.gte(min_pixels).unmask(0))

    footprint = post.mask().reduce(ee.Reducer.min()).gt(0)
    return flood.updateMask(footprint).rename("flood"), footprint.rename("covered")


def peak_flood(region, **params):
    """Flood extent at the peak: union of the 12/9 (orbit 175) and 13/9 (orbit 7) passes."""
    f12, c12 = flood_map(region, "2023-09-12", "2023-09-13", **params)
    f13, c13 = flood_map(region, "2023-09-13", "2023-09-14", **params)
    flood = f12.unmask(0).Or(f13.unmask(0)).rename("flood")
    covered = c12.unmask(0).Or(c13.unmask(0)).rename("covered")
    return flood, covered


def area_km2(mask, region, scale=10):
    """Area (km²) of the pixels equal to 1 in a single-band mask."""
    value = (ee.Image(mask).gt(0).selfMask().multiply(ee.Image.pixelArea())
             .reduceRegion(ee.Reducer.sum(), region, scale, maxPixels=1e10, bestEffort=True)
             .values().get(0))
    return ee.Number(value).divide(1e6).getInfo()


# One-at-a-time variations of the method (the baseline is the default parameters).
# Used by the robustness tests in notebooks 02 (against Copernicus EMS) and 05 (effect on the final numbers).
SCENARIOS = {
    "baseline": {},
    "change < -2 dB": {"diff_thr": -2},
    "change < -4 dB": {"diff_thr": -4},
    "post < -13 dB": {"post_thr": -13},
    "post < -17 dB": {"post_thr": -17},
    "speckle filter 30 m": {"smooth_m": 30},
    "speckle filter 80 m": {"smooth_m": 80},
    "min cluster 4 px": {"min_pixels": 4},
    "min cluster 16 px": {"min_pixels": 16},
    "max slope 3°": {"max_slope": 3},
    "max slope 10°": {"max_slope": 10},
    "before-window from 1 Aug": {"pre_start": "2023-08-01"},
    "before-window from 1 Jul": {"pre_start": "2023-07-01"},
    "before-window 25 Aug–4 Sep": {"pre_start": "2023-08-25"},
}
