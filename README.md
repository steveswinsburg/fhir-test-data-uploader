# 🏥 FHIR Test Data Uploader

A Python utility for uploading FHIR JSON / NDJSON files to FHIR servers using PUT requests with specific resource IDs. This tool is designed to help you quickly populate FHIR servers with test data for development, testing, and demonstration purposes.

## ✨ Features

- 📁 Batch upload multiple FHIR JSON files
- 📄 Streaming support for NDJSON files 
- 🔒 Support for Basic Authentication
- 🎯 Optional filtering by FHIR resource type
- 🧭 Automatic resource-type order inference from references (full scan by default, sampling optional)
- 📚 Explicit resource-type upload ordering for dependency-sensitive loads
- 📊 Progress reporting with status codes
- 🔄 Automatic resource type and ID detection from filename
- 🆔 PUT requests with specific resource IDs (not server-generated)
- 🧩 Chunked uploads with automatic resume for very large datasets (e.g. 1M+ records)
- 🛡️ Error handling and detailed logging
- 🐚 Shell script wrapper for easy execution

## 📋 Prerequisites

- **Python 3.6+**
- **pip** (Python package installer)
- Access to a FHIR R4 compliant server

## 🚀 Installation

### Quick Start (Recommended)

The easiest way to get started is to simply clone the repository and use the shell script wrapper - it handles all the Python environment setup automatically:

```bash
# Clone the repository
git clone https://github.com/steveswinsburg/fhir-test-data-uploader.git
cd fhir-test-data-uploader

# Make the script executable (if needed)
chmod +x upload.sh

# Run it! The script will create venv and install dependencies automatically
./upload.sh --data ./your-fhir-data --host http://localhost:8080/fhir
```

### Manual Setup (Advanced Users)

If you prefer to manage the Python environment yourself:

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run directly
python upload.py --data ./your-fhir-data --host http://localhost:8080/fhir
```

## 📁 Data Format Requirements

Your FHIR resource files can be provided as either individual JSON resources or NDJSON streams.

### Individual JSON Files

Individual FHIR JSON files must follow this naming convention:
```
ResourceType-identifier.json
```

**Examples:**
- `Patient-john-doe.json` → PUT to `/Patient/john-doe`
- `Patient-dietrich-kimbra-althea.json` → PUT to `/Patient/dietrich-kimbra-althea`
- `AllergyIntolerance-peanut-allergy.json` → PUT to `/AllergyIntolerance/peanut-allergy`
- `Observation-blood-pressure-001.json` → PUT to `/Observation/blood-pressure-001`

The script automatically:
- Extracts the resource type from the filename prefix
- Extracts the resource ID from everything after the first dash
- Uses PUT requests to `/ResourceType/ID` endpoints
- Ensures the JSON `id` field matches the URL ID

### NDJSON Files

NDJSON files are also supported and will be streamed.

Recommended naming conventions:
```
Patient.ndjson
Patient-part1.ndjson
Observation.ndjson
```

Each line must contain a complete FHIR resource.

## 🏃‍♂️ Usage

### Easy Way: Using the Shell Script (Recommended)

The `upload.sh` script handles virtual environment setup and dependency installation automatically:

```bash
# Basic usage
./upload.sh --data ./fhir-data --host http://localhost:8080/fhir

# With authentication
./upload.sh \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --user yourusername \
  --password yourpassword

# Upload specific resource type only
./upload.sh \
  --data ./fhir-data \
  --host http://localhost:8080/fhir \
  --type Patient

# Let the uploader infer resource-type order automatically
./upload.sh \
  --data ./fhir-data \
  --host http://localhost:8080/fhir

# Override the inferred order with an explicit dependency order
./upload.sh \
  --data ./fhir-data \
  --host http://localhost:8080/fhir \
  --type-order Patient,Practitioner,Organization,Encounter,Observation

# Faster inference on huge NDJSON datasets (sampling mode)
./upload.sh \
  --data ./fhir-data \
  --host http://localhost:8080/fhir \
  --type-order-scan-mode sample \
  --type-order-sample-size 1000

# Enable verbose debug logging
./upload.sh \
  --data ./fhir-data \
  --host http://localhost:8080/fhir \
  --debug

# Upload a huge dataset (e.g. 1 million records) in resumable chunks of 10,000
./upload.sh \
  --data ./fhir-data \
  --host http://localhost:8080/fhir \
  --chunk-size 10000
# Re-run the exact same command to continue from where it left off.
# Keep re-running until it exits with code 0 (fully complete), e.g.:
while true; do
  ./upload.sh --data ./fhir-data --host http://localhost:8080/fhir --chunk-size 10000
  [ $? -ne 2 ] && break
done

# Start over from scratch, ignoring any saved progress
./upload.sh \
  --data ./fhir-data \
  --host http://localhost:8080/fhir \
  --restart

# Upload NDJSON resources with concurrent workers for higher throughput
./upload.sh \
  --data ./fhir-data \
  --host http://localhost:8080/fhir \
  --concurrency 8

# Example with local HAPI FHIR server
./upload.sh --data ../au-fhir-test-data/au-core --host http://localhost:8080/fhir
```

### Manual Way: Direct Python Usage

```bash
# Activate virtual environment first
source venv/bin/activate

# Basic usage
python upload.py --data ./fhir-data --host http://localhost:8080/fhir

# With authentication
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --user yourusername \
  --password yourpassword

# Upload specific resource type
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --type Patient

# Let the uploader infer resource-type order automatically
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir

# Override the inferred order with an explicit dependency order
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --type-order Patient,Practitioner,Organization,Encounter,Observation

# Faster inference on huge NDJSON datasets (sampling mode)
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --type-order-scan-mode sample \
  --type-order-sample-size 1000

# Enable verbose debug logging
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --debug

# Upload a huge dataset in resumable chunks, pausing after every 10,000 records
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --chunk-size 10000
# Re-run the same command to resume automatically from the saved progress file.

# Ignore saved progress and re-upload everything from the start
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --restart

# Upload NDJSON resources with concurrent workers for higher throughput
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --concurrency 8
```

### Command Line Arguments

| Argument | Required | Description | Example |
|----------|----------|-------------|---------|
| `--data` | ✅ | Directory containing FHIR JSON files | `./fhir-data` |
| `--host` | ✅ | Base URL of FHIR server (include `/fhir`) | `http://localhost:8080/fhir` |
| `--user` | ❌ | Username for Basic Authentication | `admin` |
| `--password` | ❌ | Password for Basic Authentication | `password123` |
| `--type` | ❌ | Filter uploads to specific resource type | `Patient` |
| `--type-order` | ❌ | Comma-separated resource types to upload in order | `Patient,Practitioner,Observation` |
| `--type-order-scan-mode` | ❌ | NDJSON dependency inference mode when `--type-order` is not provided: `full` (default) or `sample` | `sample` |
| `--type-order-sample-size` | ❌ | Per-file sample size used when `--type-order-scan-mode=sample` | `1000` |
| `--chunk-size` | ❌ | Opt in to chunked uploads: max resources to process per run before pausing; enables progress tracking/resume | `10000` |
| `--progress-file` | ❌ | Path to the progress tracking file when using `--chunk-size` (default: `<data>/.upload-progress.<host>.json`) | `./progress.json` |
| `--restart` | ❌ | Ignore any existing progress file and re-upload everything from the start (requires `--chunk-size`) | `--restart` |
| `--concurrency` | ❌ | Number of concurrent upload workers for NDJSON resources (default: `1`, sequential). Only used when a resource type has no same-type references (e.g. `Organization.partOf`); otherwise falls back to sequential automatically | `8` |
| `--debug` | ❌ | Enable verbose debug logging | `--debug` |

Notes:
- `--type` and `--type-order` cannot be used together.
- `--type-order-sample-size` must be greater than `0` when `--type-order-scan-mode=sample`.
- `--progress-file` and `--restart` require `--chunk-size`; without `--chunk-size`, no progress is tracked and the upload always runs start-to-finish (previous behavior).
- With `--concurrency` > 1, many workers can write to the same FHIR resource type at once. The underlying database may occasionally abort one side of a write/write conflict (e.g. a Postgres deadlock) under high concurrency; the uploader detects this and automatically retries the affected PUT with exponential backoff, since the original write was rolled back and is safe to resend. If retries are exhausted the resource is logged as failed and the run continues. If this happens frequently, try a lower `--concurrency` value.

## 🧩 Chunked Uploads & Resume (Large Datasets)

For very large datasets (e.g. 1 million+ records), uploading everything in a single run can take a long time and is risky if interrupted. This is **opt-in**: by default (no `--chunk-size`), the uploader behaves exactly as before — no progress file is created and the upload always runs start-to-finish. Pass `--chunk-size` to enable chunking and progress tracking:

- **`--chunk-size N`**: stop after processing `N` resources and save progress. The process exits with status code `2` to indicate more work remains, or `0` once everything is uploaded.
- **Automatic resume**: simply re-run the exact same command (still with `--chunk-size`) — it picks up exactly where it left off (per NDJSON file, by resource count; per individual JSON file, by filename).
- **Progress file**: by default stored at `<data>/.upload-progress.<host>.json`. Override the location with `--progress-file`.
- **`--restart`**: ignore any saved progress and start the upload from scratch.
- Progress is also checkpointed periodically (every 1000 records) during a run, so an unexpected crash or `Ctrl+C` still allows resuming without reprocessing everything.

Example — upload 1 million records in chunks of 10,000, looping automatically until complete:

```bash
while true; do
  ./upload.sh --data ./fhir-data --host http://localhost:8080/fhir --chunk-size 10000
  [ $? -ne 2 ] && break
done
```

### Shell Script Features

The `upload.sh` script provides additional conveniences:
- ✅ Automatically creates and activates Python virtual environment
- ✅ Installs dependencies from `requirements.txt`
- ✅ Provides helpful usage examples and error messages
- ✅ Handles parameter validation
- ✅ Shows the actual Python command being executed

## 🔗 Handling Resource Dependencies

FHIR resources often reference other resources (e.g., Observations reference Patients). If your FHIR server validates references:

1. **Use the default inferred order**: When uploading a whole directory with NDJSON present, the tool infers resource-type order from references (full scan by default)
2. **Multiple passes**: Run the upload script multiple times - previously failed resources may succeed once their dependencies exist
3. **Server idempotency**: Ensure your FHIR server handles duplicate submissions gracefully
4. **Override when needed**: Use `--type-order` if you already know the desired order or want to override the inferred one

### Recommended Upload Order

The inferred order should usually handle this automatically, but a common manual order is:

1. `Patient` resources
2. `Practitioner` resources  
3. `Organization` resources
4. `Encounter` resources
5. `Observation`, `AllergyIntolerance`, `Condition` resources
6. Other dependent resources

## ⚠️ Important Notes

- **Resource IDs**: Uses PUT requests with specific IDs from filenames (not server-generated IDs)
- **Data Privacy**: Never upload real patient data to public test servers
- **Rate Limiting**: Some servers may have rate limits - add delays if needed
- **Authentication**: Currently supports HTTP Basic Auth only
- **FHIR Version**: Designed for FHIR R4
- **Content Type**: Uses `application/fhir+json` header
- **ID Validation**: Ensures JSON `id` field matches the filename-derived ID
- **NDJSON Logging**: Large NDJSON uploads report periodic progress instead of logging every resource
- **Order Inference Mode**: Default is `--type-order-scan-mode full` for maximum dependency-detection accuracy; use `sample` for faster inference on very large datasets

## 🛠️ Troubleshooting

### Common Issues

**"No files found for resource type"**
- Check your file naming convention
- Ensure files end with `.json`
- Verify the `--data` directory path

**HTTP 401 Unauthorized**
- Verify username and password
- Check if the server requires different authentication

**HTTP 422 Unprocessable Entity**
- Resource validation failed
- Check for missing required fields
- Ensure referenced resources exist

## 📝 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---

**Made with ❤️ for the FHIR community**