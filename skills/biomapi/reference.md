# BiomAPI Data Reference

Complete schema reference for BiomAPI JSON responses. Use this to interpret extraction results, validate values, and answer clinical questions about biometry data.

## Supported Devices

### Biometers (PDF/image extraction)

| Device | Manufacturer | PK Support |
|--------|-------------|------------|
| Aladdin | Topcon | No |
| Anterion | Heidelberg | Yes |
| Argos | Alcon | No |
| EyeStar ES900 | Haag-Streit | Yes |
| Galilei | Ziemer | Yes (PK only) |
| IOLMaster 700 | Zeiss | Yes |
| Lenstar LS 900 | Haag-Streit | No |
| MS-39 | CSO | Yes |
| OA-2000 | Tomey | No |
| Pentacam AXL | Oculus | Yes (PK only) |
| Other | Other | — |

PK Support = device can provide posterior keratometry measurements.

## Core Biometry Data (`data`)

### Biometric Measurements (per eye)

| Field | Type | Unit | Range | Description |
|-------|------|------|-------|-------------|
| `AL` | float | mm | 14.0–40.0 | Axial Length |
| `ACD` | float | mm | 1.0–6.0 | Anterior Chamber Depth |
| `K1_magnitude` | float | D | 20.0–99.0 | Flat keratometry power |
| `K1_axis` | int | ° | 0–180 | Flat keratometry axis |
| `K2_magnitude` | float | D | 20.0–99.0 | Steep keratometry power |
| `K2_axis` | int | ° | 0–180 | Steep keratometry axis |
| `WTW` | float | mm | 7.0–15.0 | White-to-White corneal diameter |
| `LT` | float | mm | 0.1–7.0 | Lens Thickness |
| `CCT` | int | um | 200–900 | Central Corneal Thickness |
| `lens_status` | enum | — | see below | Current lens status |
| `post_refractive` | enum | — | see below | Post-refractive surgery history |
| `keratometric_index` | float | — | 1.330–1.338 | Keratometric index (typically 1.3375) |

All measurements are optional (nullable). A `null` value means the measurement was not present on the report.

### Typical Ranges (clinical context)

These are NOT validation ranges — they're typical adult values for clinical context:

| Measurement | Typical Range | Notes |
|-------------|--------------|-------|
| AL | 22.0–25.0 mm | <22 = short eye (hyperopic), >26 = long eye (myopic) |
| ACD | 2.5–4.0 mm | Shallower in older patients |
| K1/K2 | 40.0–47.0 D | Astigmatism = K2 - K1 |
| WTW | 11.0–12.5 mm | Relevant for ICL/IOL sizing |
| LT | 3.5–5.5 mm | Increases with age and cataract |
| CCT | 500–570 um | Relevant for IOP correction |

### Enum Values

**Lens Status**: `Phakic`, `Phakic IOL`, `Pseudophakic`, `Aphakic`

**Post-Refractive**: `None`, `Myopic LVC`, `Hyperopic LVC`, `Radial Keratotomy`

**Gender**: `Male`, `Female`

### Patient Data

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Patient name acronym (e.g., "JDD" for John Douglas Doe) |
| `id` | string | Patient ID or medical record number |
| `date_of_birth` | date | ISO format (YYYY-MM-DD) |
| `gender` | enum | Male, Female |

Patient names are automatically converted to uppercase acronyms for privacy.

## Extra Data (`extra_data`)

Optional fields returned when the source report contains additional data.

### Posterior Keratometry

Present when the device supports posterior corneal measurements. Important for toric IOL calculations (Barrett Toric, EVO formula).

| Field | Type | Unit | Range | Description |
|-------|------|------|-------|-------------|
| `pk_device_name` | enum | — | see table above | Device used for PK measurement |
| `PK1_magnitude` | float | D | 2.0–8.0 | Posterior flat keratometry |
| `PK1_axis` | int | ° | 0–180 | Posterior flat axis |
| `PK2_magnitude` | float | D | 2.0–8.0 | Posterior steep keratometry |
| `PK2_axis` | int | ° | 0–180 | Posterior steep axis |

Posterior keratometry is measured per eye, same structure as anterior K values but with lower diopter range.

### Notes

`extra_data.notes` — free-text string for additional clinical notes found on the report.

## Metadata (`metadata`)

### Extraction Info

| Field | Description |
|-------|-------------|
| `schema_version` | Biometry schema version |
| `app_version` | BiomAPI server version |
| `extraction.method` | `BiomAI` (LLM extraction) or `BiomDIRECT` (direct/edited) |
| `extraction.timestamp` | ISO timestamp of when extraction occurred |
| `extraction.llm` | Model used for extraction (BiomAI only) |
| `extraction.llm_api_metrics` | Token usage stats (BiomAI only) |
| `extraction.llm_performance` | Response time and retry info (BiomAI only) |
| `extraction.input_schema_version` | Input schema version on JSON round-trips or direct/manual data |
| `extraction.source_app` | Client-declared source application for BiomDIRECT data |

### BiomPIN

| Field | Description |
|-------|-------------|
| `pin` | Sharing code in format `word-word-123456` |
| `expires_at` | ISO timestamp when the PIN expires (default: 31 days / 744 hours) |
| `db_id` | BiomPIN database instance identifier |
