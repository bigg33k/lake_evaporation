# Lake Evaporation Estimator

A Streamlit web app that estimates open-water evaporation for a lake using weather data from a [Weather Underground Personal Weather Station (PWS)](https://www.wunderground.com/pws/overview).

**Live app:** [lake-evaporation.streamlit.app](https://lake-evaporation.streamlit.app) *(update with your Streamlit Cloud URL)*

---

## Features

- Fetches daily weather data from any Weather Underground PWS
- Estimates evaporation using the **Penman open-water method** (FAO-56 variant)
- Solar radiation estimated via Hargreaves-Samani ΔT method
- Wind speed corrected to 2 m reference height
- Results in mm, inches, acre-feet, and million gallons
- Daily charts for evaporation, temperature, humidity, wind, and precipitation
- Cumulative volume-loss chart
- Monthly summary table
- CSV download

---

## Method

The FAO-56 Penman-Monteith equation is adapted for open water by setting surface resistance **rₛ = 0** and using a water-surface albedo of **0.05**:

```
E = [ 0.408·Δ·(Rn − G) + γ·(37/(T+273))·u₂·(es − ea) ] / (Δ + γ)
```

| Symbol | Description |
|--------|-------------|
| Δ | Slope of saturation vapour pressure curve (kPa/°C) |
| Rₙ | Net radiation at the water surface (MJ m⁻² day⁻¹) |
| G | Water heat flux ≈ 0 for daily timestep |
| γ | Psychrometric constant (kPa/°C) |
| u₂ | Wind speed at 2 m height (m/s) |
| es − ea | Vapour pressure deficit (kPa) |

Solar radiation is estimated from the daily temperature range (Hargreaves-Samani) using the station latitude, since Weather Underground only reports peak instantaneous W/m², not a daily integrated total.

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/bigg33k/lake_evaporation.git
cd lake_evaporation
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Add your Weather Underground API key

Create `.streamlit/secrets.toml` (this file is gitignored):

```toml
WU_API_KEY = "your_api_key_here"
```

Get a free key at [wunderground.com → Account → API Keys](https://www.wunderground.com/member/api-keys).

### 4. Run

```bash
streamlit run app.py
```

---

## Deploying to Streamlit Cloud

1. Fork or push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect the repo
3. Under **Settings → Secrets**, add:
   ```toml
   WU_API_KEY = "your_api_key_here"
   ```
4. Deploy — the API key is encrypted and never stored in the repo

---

## Configuration

| Sidebar setting | Default | Description |
|----------------|---------|-------------|
| PWS Station ID | `KMESHAPL32` | Any Weather Underground PWS station |
| Date range | Last 30 days | Up to ~500 days (API rate limit: 1500 calls/day) |
| Lake area | 910 acres | Used to convert depth to volume |
| Latitude | 33.43 °N | Used for solar radiation estimation |
| Elevation | 550 m | Used for atmospheric pressure |
| Anemometer height | 10 m | Wind sensor height for log-law correction |

---

## Dependencies

- [Streamlit](https://streamlit.io)
- [Pandas](https://pandas.pydata.org)
- [NumPy](https://numpy.org)
- [Plotly](https://plotly.com/python/)
- [Requests](https://docs.python-requests.org)

---

## Reference

Allen, R.G., Pereira, L.S., Raes, D., Smith, M. (1998). *Crop evapotranspiration — Guidelines for computing crop water requirements.* FAO Irrigation and Drainage Paper 56. Food and Agriculture Organization of the United Nations, Rome.
