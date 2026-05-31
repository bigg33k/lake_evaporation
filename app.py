"""
Lake Evaporation Estimator
Fetches daily weather data from a Weather Underground PWS and estimates
open-water evaporation using the Penman equation (FAO-56 open-water variant).
"""

import math
import time
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Lake Evaporation Estimator",
    page_icon="💧",
    layout="wide",
)

st.title("💧 Lake Evaporation Estimator")
st.caption(
    "Estimates open-water evaporation (Penman method) using "
    "Weather Underground PWS data — station **KMESHAPL32**"
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
api_key = st.secrets["WU_API_KEY"]

with st.sidebar:
    st.header("⚙️ Configuration")

    fetch_btn = st.button(
        "📥 Fetch Data & Calculate", type="primary", use_container_width=True
    )

    st.divider()

    station_id = st.text_input("PWS Station ID", value="KMESHAPL32")

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input(
            "Start Date", value=date.today() - timedelta(days=30)
        )
    with col2:
        end_date = st.date_input("End Date", value=date.today() - timedelta(days=1))

    st.divider()

    lake_area_acres = st.number_input(
        "Lake Area (acres)", value=910.0, min_value=1.0, step=10.0
    )

    st.subheader("Advanced")
    latitude = st.number_input(
        "Latitude (°N)",
        value=33.43,
        min_value=-90.0,
        max_value=90.0,
        step=0.01,
        help="Used to estimate solar radiation when not measured by the station.",
    )
    elevation_m = st.number_input(
        "Elevation (m)",
        value=550.0,
        min_value=0.0,
        step=10.0,
        help="Affects atmospheric pressure and psychrometric constant.",
    )
    anem_height_m = st.number_input(
        "Anemometer Height (m)",
        value=10.0,
        min_value=1.0,
        step=0.5,
        help="Height of the wind sensor. Most home PWS use 10 m.",
    )


# ── Weather Underground helpers ───────────────────────────────────────────────
WU_BASE = "https://api.weather.com/v2/pws/history/daily"


def fetch_daily(api_key: str, station_id: str, day: date) -> Optional[dict]:
    """Return the daily summary dict for one day, or None on failure."""
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",  # metric
        "date": day.strftime("%Y%m%d"),
        "apiKey": api_key,
        "numericPrecision": "decimal",
    }
    try:
        r = requests.get(WU_BASE, params=params, timeout=15)
        r.raise_for_status()
        observations = r.json().get("observations", [])
        return observations[0] if observations else None
    except Exception as exc:
        st.warning(f"Could not fetch {day}: {exc}")
        return None


def fetch_station_meta(api_key: str, station_id: str) -> dict:
    """Fetch station metadata (lat/lon/elevation)."""
    try:
        r = requests.get(
            "https://api.weather.com/v2/pws/metadata",
            params={"stationId": station_id, "format": "json", "apiKey": api_key},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def load_weather(
    api_key: str, station_id: str, start: date, end: date
) -> pd.DataFrame:
    """Fetch a range of daily summaries and return a DataFrame."""
    days = pd.date_range(start, end, freq="D")
    records = []
    progress = st.progress(0, text="Fetching weather data…")
    for i, day in enumerate(days):
        obs = fetch_daily(api_key, station_id, day.date())
        if obs:
            m = obs.get("metric", {})
            records.append(
                {
                    "date": day.date(),
                    "temp_max_c": m.get("tempHigh"),
                    "temp_min_c": m.get("tempLow"),
                    "temp_avg_c": m.get("tempAvg"),
                    "dewpoint_avg_c": m.get("dewptAvg"),
                    "humidity_avg_pct": m.get("humidityAvg"),
                    "wind_avg_ms": (m.get("windspeedAvg") or 0) / 3.6,  # km/h → m/s
                    "pressure_avg_hpa": m.get("pressureAvg"),
                    "solar_rad_wm2": None,  # solarRadiationHigh is peak W/m², not daily total — use Hargreaves instead
                    "precip_mm": m.get("precipTotal"),
                }
            )
        time.sleep(0.15)  # stay well under rate limits
        progress.progress((i + 1) / len(days), text=f"Fetched {i+1}/{len(days)} days…")
    progress.empty()
    return pd.DataFrame(records)


# ── Penman open-water evaporation ─────────────────────────────────────────────

def sat_vp(t_c: float) -> float:
    """Saturation vapour pressure [kPa] at temperature t_c [°C]."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def slope_vp(t_c: float) -> float:
    """Slope of saturation vapour pressure curve [kPa/°C]."""
    es = sat_vp(t_c)
    return 4098 * es / (t_c + 237.3) ** 2


def psychro_const(pressure_kpa: float) -> float:
    """Psychrometric constant γ [kPa/°C]."""
    return 0.000665 * pressure_kpa


def atm_pressure(elevation_m: float) -> float:
    """Mean atmospheric pressure [kPa] at elevation [m]."""
    return 101.325 * ((293 - 0.0065 * elevation_m) / 293) ** 5.26


def wind_2m(u_z: float, z: float) -> float:
    """Adjust wind speed to 2 m height using log-law."""
    return u_z * (4.87 / math.log(67.8 * z - 5.42))


def extraterrestrial_radiation(lat_deg: float, day_of_year: int) -> float:
    """Extraterrestrial radiation Ra [MJ/m²/day]."""
    lat = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi / 365 * day_of_year)
    delta = 0.409 * math.sin(2 * math.pi / 365 * day_of_year - 1.39)
    ws = math.acos(-math.tan(lat) * math.tan(delta))
    ra = (
        24 * 60 / math.pi
        * 0.0820
        * dr
        * (ws * math.sin(lat) * math.sin(delta) + math.cos(lat) * math.cos(delta) * math.sin(ws))
    )
    return ra


def estimate_solar_radiation(t_max: float, t_min: float, ra: float, coastal: bool = False) -> float:
    """Estimate daily solar radiation [MJ/m²/day] via Hargreaves-Samani."""
    krs = 0.16 if not coastal else 0.19  # empirical coefficient
    return krs * math.sqrt(max(t_max - t_min, 0)) * ra


def net_radiation_open_water(
    rs_mj: float, t_max_c: float, t_min_c: float, ea_kpa: float, ra: float
) -> float:
    """Net radiation at the open-water surface [MJ/m²/day]."""
    albedo = 0.05  # open water
    rns = (1 - albedo) * rs_mj

    # Clear-sky radiation
    rso = (0.75 + 2e-5 * elevation_m) * ra  # uses closure over elevation_m

    # Ratio clipped to [0.3, 1.0]
    ratio = min(max(rs_mj / rso if rso > 0 else 1.0, 0.3), 1.0)

    sigma = 4.903e-9  # Stefan-Boltzmann [MJ/K⁴/m²/day]
    t_max_k = t_max_c + 273.16
    t_min_k = t_min_c + 273.16
    rnl = (
        sigma
        * (t_max_k**4 + t_min_k**4)
        / 2
        * (0.34 - 0.14 * math.sqrt(max(ea_kpa, 0.001)))
        * (1.35 * ratio - 0.35)
    )
    return rns - rnl


def penman_open_water_mm(row: pd.Series, lat: float, elev: float, z_wind: float) -> float:
    """
    Open-water evaporation [mm/day] using the Penman/FAO-56 approach
    with rs = 0 (no stomatal resistance).
    Returns NaN if essential inputs are missing.
    """
    try:
        t_max = float(row["temp_max_c"])
        t_min = float(row["temp_min_c"])
        t_mean = float(row["temp_avg_c"]) if pd.notna(row["temp_avg_c"]) else (t_max + t_min) / 2

        doy = row["date"].timetuple().tm_yday
        ra = extraterrestrial_radiation(lat, doy)

        # Estimate solar radiation from daily temperature range (Hargreaves-Samani).
        # WU only provides peak instantaneous W/m², not a daily total, so we
        # always derive Rs from ΔT to avoid a ~3× overestimate.
        rs_mj = estimate_solar_radiation(t_max, t_min, ra)

        # Vapour pressures
        es = (sat_vp(t_max) + sat_vp(t_min)) / 2  # kPa

        if pd.notna(row["dewpoint_avg_c"]):
            ea = sat_vp(float(row["dewpoint_avg_c"]))
        elif pd.notna(row["humidity_avg_pct"]):
            ea = es * float(row["humidity_avg_pct"]) / 100
        else:
            return float("nan")

        vpd = es - ea  # vapour pressure deficit [kPa]

        # Atmospheric pressure & psychrometric constant
        P = atm_pressure(elev)
        gamma = psychro_const(P)

        # Wind at 2 m
        u2 = wind_2m(float(row["wind_avg_ms"]), z_wind)

        # Slope of saturation curve
        delta = slope_vp(t_mean)

        # Net radiation (G ≈ 0 for daily lake, small compared to Rn)
        rn = net_radiation_open_water(rs_mj, t_max, t_min, ea, ra)

        # Penman open-water (rs=0 → no 0.34*u2 in denominator)
        numerator = 0.408 * delta * rn + gamma * (37 / (t_mean + 273)) * u2 * vpd
        denominator = delta + gamma
        et_mm = numerator / denominator

        return max(et_mm, 0.0)  # can't have negative evaporation
    except Exception:
        return float("nan")


# ── Volume/area conversions ───────────────────────────────────────────────────
ACRES_TO_M2 = 4046.8564
GALLONS_PER_ACRE_INCH = 27_154
MM_TO_INCHES = 1 / 25.4


def evap_to_acre_feet(mm: float, acres: float) -> float:
    """Convert mm of evaporation over a lake to acre-feet."""
    return mm / 1000 * acres * ACRES_TO_M2 / 1233.48


def evap_to_gallons(mm: float, acres: float) -> float:
    return mm * MM_TO_INCHES * acres * GALLONS_PER_ACRE_INCH


# ── Main logic (triggered by button) ─────────────────────────────────────────
if fetch_btn:
    if start_date >= end_date:
        st.error("Start date must be before end date.")
        st.stop()

    # Optional: pull station metadata to auto-fill lat/lon
    with st.spinner("Fetching station metadata…"):
        meta = fetch_station_meta(api_key, station_id)
    if meta:
        obs_meta = meta.get("observations", [{}])[0] if "observations" in meta else meta
        st.sidebar.caption(
            f"Station: {obs_meta.get('neighborhood', station_id)}  \n"
            f"Lat {obs_meta.get('lat', '?')}  Lon {obs_meta.get('lon', '?')}"
        )

    # Fetch weather
    df = load_weather(api_key, station_id, start_date, end_date)

    if df.empty:
        st.error("No data returned. Check your API key, station ID, and date range.")
        st.stop()

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # Fill missing temp_avg from max/min
    mask = df["temp_avg_c"].isna()
    df.loc[mask, "temp_avg_c"] = (df.loc[mask, "temp_max_c"] + df.loc[mask, "temp_min_c"]) / 2

    # Compute evaporation
    df["evap_mm"] = df.apply(
        penman_open_water_mm,
        axis=1,
        lat=latitude,
        elev=elevation_m,
        z_wind=anem_height_m,
    )
    df["evap_in"] = df["evap_mm"] * MM_TO_INCHES
    df["evap_acre_ft"] = df.apply(
        lambda r: evap_to_acre_feet(r["evap_mm"], lake_area_acres), axis=1
    )
    df["evap_mgal"] = df.apply(
        lambda r: evap_to_gallons(r["evap_mm"], lake_area_acres) / 1_000_000, axis=1
    )
    df["cumul_evap_acre_ft"] = df["evap_acre_ft"].cumsum()
    df["cumul_evap_in"] = df["evap_in"].cumsum()

    # ── KPI row ────────────────────────────────────────────────────────────────
    total_days = df["evap_mm"].notna().sum()
    total_evap_mm = df["evap_mm"].sum()
    total_evap_af = df["evap_acre_ft"].sum()
    mean_evap_mm = df["evap_mm"].mean()
    total_evap_mgal = df["evap_mgal"].sum()

    st.subheader("Summary")
    k1, k2, k3 = st.columns(3)
    k1.metric("Days", f"{total_days}")
    k2.metric("Mean Daily Evap", f"{mean_evap_mm:.1f} mm  ({mean_evap_mm*MM_TO_INCHES:.2f} in)")
    k3.metric("Volume Lost", f"{total_evap_af:.1f} ac-ft")
    st.caption(f"≈ {total_evap_mgal:.2f} million gallons over {total_days} days")

    # ── Charts ─────────────────────────────────────────────────────────────────
    st.subheader("Daily Evaporation & Weather")

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        subplot_titles=(
            "Daily Evaporation (mm/day)",
            "Temperature (°C) & Humidity (%)",
            "Wind Speed (m/s) & Precipitation (mm)",
        ),
        vertical_spacing=0.08,
    )

    fig.add_trace(
        go.Bar(
            x=df["date"], y=df["evap_mm"], name="Evaporation (mm)", marker_color="#1f77b4"
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Scatter(x=df["date"], y=df["temp_max_c"], name="T max (°C)", line=dict(color="red")),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["date"], y=df["temp_min_c"], name="T min (°C)", line=dict(color="blue")),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["date"], y=df["humidity_avg_pct"], name="Humidity (%)",
            line=dict(color="green", dash="dot"), yaxis="y4",
        ),
        row=2, col=1,
    )

    fig.add_trace(
        go.Scatter(x=df["date"], y=df["wind_avg_ms"], name="Wind (m/s)", line=dict(color="orange")),
        row=3, col=1,
    )
    fig.add_trace(
        go.Bar(x=df["date"], y=df["precip_mm"], name="Precip (mm)", marker_color="lightblue"),
        row=3, col=1,
    )

    fig.update_layout(height=700, legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fig, use_container_width=True)

    # Cumulative chart
    fig2 = go.Figure()
    fig2.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["cumul_evap_acre_ft"],
            fill="tozeroy",
            name="Cumulative volume lost (acre-ft)",
            line=dict(color="#1f77b4"),
        )
    )
    fig2.update_layout(
        title=f"Cumulative Evaporation Volume — {lake_area_acres:,.0f}-Acre Lake",
        xaxis_title="Date",
        yaxis_title="Acre-Feet",
        height=350,
    )
    st.plotly_chart(fig2, use_container_width=True)

    # ── Water-level drop ───────────────────────────────────────────────────────
    st.subheader("Estimated Water-Level Drop")
    wl_col1, wl_col2 = st.columns(2)
    total_evap_in = total_evap_mm * MM_TO_INCHES
    with wl_col1:
        st.metric(
            "Evaporation depth (period total)",
            f"{total_evap_in:.2f} inches  ({total_evap_mm:.0f} mm)",
            help="Uniform drop in lake surface level due to evaporation alone (assumes constant area).",
        )
    with wl_col2:
        # Spring-fed note: net drop depends on inflow
        st.info(
            "**Spring-fed note:** The actual water-level change equals  \n"
            "**Evaporation − Spring inflow − Other losses + Precipitation**.  \n"
            "The figures above show evaporation loss only."
        )

    # ── Monthly summary table ─────────────────────────────────────────────────
    st.subheader("Monthly Summary")
    df["month"] = df["date"].dt.to_period("M")
    monthly = (
        df.groupby("month")
        .agg(
            days=("evap_mm", "count"),
            evap_mm=("evap_mm", "sum"),
            evap_in=("evap_in", "sum"),
            evap_acre_ft=("evap_acre_ft", "sum"),
            evap_mgal=("evap_mgal", "sum"),
            temp_avg_c=("temp_avg_c", "mean"),
            humidity_avg_pct=("humidity_avg_pct", "mean"),
            wind_avg_ms=("wind_avg_ms", "mean"),
        )
        .reset_index()
    )
    monthly["month"] = monthly["month"].astype(str)
    monthly = monthly.rename(
        columns={
            "month": "Month",
            "days": "Days",
            "evap_mm": "Evap (mm)",
            "evap_in": "Evap (in)",
            "evap_acre_ft": "Volume (acre-ft)",
            "evap_mgal": "Volume (Mgal)",
            "temp_avg_c": "Avg Temp (°C)",
            "humidity_avg_pct": "Avg RH (%)",
            "wind_avg_ms": "Avg Wind (m/s)",
        }
    )
    st.dataframe(
        monthly.style.format(
            {
                "Evap (mm)": "{:.1f}",
                "Evap (in)": "{:.2f}",
                "Volume (acre-ft)": "{:.2f}",
                "Volume (Mgal)": "{:.3f}",
                "Avg Temp (°C)": "{:.1f}",
                "Avg RH (%)": "{:.0f}",
                "Avg Wind (m/s)": "{:.2f}",
            }
        ),
        use_container_width=True,
    )

    # ── Raw data download ─────────────────────────────────────────────────────
    st.subheader("Raw Daily Data")
    display_cols = [
        "date", "temp_max_c", "temp_min_c", "temp_avg_c",
        "dewpoint_avg_c", "humidity_avg_pct", "wind_avg_ms",
        "precip_mm", "solar_rad_wm2",
        "evap_mm", "evap_in", "evap_acre_ft", "evap_mgal",
    ]
    st.dataframe(
        df[display_cols].style.format(
            {c: "{:.2f}" for c in display_cols if c != "date"},
            na_rep="—",
        ),
        use_container_width=True,
    )
    csv = df[display_cols].to_csv(index=False).encode()
    st.download_button(
        "⬇️ Download CSV",
        csv,
        file_name=f"lake_evaporation_{station_id}_{start_date}_{end_date}.csv",
        mime="text/csv",
    )

else:
    # ── Landing / help ─────────────────────────────────────────────────────────
    st.info("Set your date range and click **📥 Fetch Data & Calculate** in the sidebar to begin.")

    with st.expander("ℹ️ How it works"):
        st.markdown(
            """
### Method: Penman Open-Water Evaporation (FAO-56 variant)

The FAO-56 Penman-Monteith equation is adapted for open water by setting the
surface (stomatal) resistance **rₛ = 0** and using a water-surface albedo of **0.05**
instead of the 0.23 used for reference grass.

$$
E = \\frac{0.408\\,\\Delta\\,(R_n - G)\\;+\\;\\gamma\\,\\frac{37}{T+273}\\,u_2\\,(e_s - e_a)}
         {\\Delta + \\gamma}
$$

| Symbol | Meaning |
|--------|---------|
| Δ | Slope of saturation vapour pressure curve (kPa/°C) |
| Rₙ | Net radiation at water surface (MJ m⁻² day⁻¹) |
| G | Water heat flux ≈ 0 for daily calculations |
| γ | Psychrometric constant (kPa/°C) |
| u₂ | Wind speed at 2 m height (m/s) |
| eₛ − eₐ | Vapour pressure deficit (kPa) |

**Solar radiation** is read from the station's sensor when available;
otherwise it is estimated from the daily temperature range
(Hargreaves-Samani method) using the station latitude.

**Wind speed** is adjusted from the anemometer height to the standard 2 m
reference height using a logarithmic profile.

### Units
- Evaporation depth (mm or inches) = uniform drop in lake surface level
- Volume (acre-feet, million gallons) = depth × lake area
- 1 acre-foot ≈ 325,851 US gallons
"""
        )
