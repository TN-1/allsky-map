import math
from datetime import datetime, timezone


def is_nighttime(lat: float | None, lng: float | None, dt: datetime | None = None) -> bool:
    """Calculates whether a geographic coordinate is currently in nighttime (sun below horizon).

    Utilizes the identical solar position and Greenwich Mean Sidereal Time (GMST)
    astronomical equations implemented in Leaflet's L.Terminator.js overlay.
    """
    if lat is None or lng is None:
        return False

    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Julian Date (valid after UNIX epoch 1970-01-01)
    epoch_ms = dt.timestamp() * 1000.0
    julian_day = (epoch_ms / 86400000.0) + 2440587.5

    # Greenwich Mean Sidereal Time in hours (USNO low-precision approximation)
    d = julian_day - 2451545.0
    gmst = (18.697374558 + 24.06570982441908 * d) % 24.0

    # Solar position in ecliptic coordinates (days since J2000.0)
    n = julian_day - 2451545.0
    mean_long = (280.460 + 0.9856474 * n) % 360.0
    mean_anom = (357.528 + 0.9856003 * n) % 360.0
    g_rad = math.radians(mean_anom)
    lambda_sun = mean_long + 1.915 * math.sin(g_rad) + 0.02 * math.sin(2.0 * g_rad)

    # Ecliptic obliquity
    t_centuries = n / 36525.0
    epsilon = 23.43929111 - t_centuries * (
        46.836769 / 3600.0 - t_centuries * (0.0001831 / 3600.0 + t_centuries * (0.00200340 / 3600.0))
    )

    # Equatorial coordinates (alpha = Right Ascension, delta = Declination)
    eps_rad = math.radians(epsilon)
    lam_rad = math.radians(lambda_sun)
    alpha = math.degrees(math.atan2(math.cos(eps_rad) * math.sin(lam_rad), math.cos(lam_rad)))
    delta = math.degrees(math.asin(math.sin(eps_rad) * math.sin(lam_rad)))

    # Local Hour Angle
    gst_deg = gmst * 15.0
    ha = (gst_deg + lng) - alpha

    # Solar altitude angle: sin(alt) = sin(lat)*sin(delta) + cos(lat)*cos(delta)*cos(ha)
    lat_rad = math.radians(lat)
    delta_rad = math.radians(delta)
    ha_rad = math.radians(ha)

    sin_alt = math.sin(lat_rad) * math.sin(delta_rad) + math.cos(lat_rad) * math.cos(delta_rad) * math.cos(ha_rad)
    return sin_alt < 0.0
