# 🏥 FHIR Test Data Uploader

A Python utility for uploading FHIR JSON files to FHIR servers using PUT requests with specific resource IDs. This tool is designed to help you quickly populate FHIR servers with test data for development, testing, and demonstration purposes.

## ✨ Features

- 📁 Batch upload multiple FHIR JSON files
- 🔒 Support for Basic Authentication
- 🎯 Optional filtering by FHIR resource type
- 📊 Progress reporting with status codes
- 🔄 Automatic resource type and ID detection from filename
- 🆔 PUT requests with specific resource IDs (not server-generated)
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

Your FHIR JSON files must follow this naming convention:
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
```

### Command Line Arguments

| Argument | Required | Description | Example |
|----------|----------|-------------|---------|
| `--data` | ✅ | Directory containing FHIR JSON files | `./fhir-data` |
| `--host` | ✅ | Base URL of FHIR server (include `/fhir`) | `http://localhost:8080/fhir` |
| `--user` | ❌ | Username for Basic Authentication | `admin` |
| `--password` | ❌ | Password for Basic Authentication | `password123` |
| `--type` | ❌ | Filter uploads to specific resource type | `Patient` |

### Shell Script Features

The `upload.sh` script provides additional conveniences:
- ✅ Automatically creates and activates Python virtual environment
- ✅ Installs dependencies from `requirements.txt`
- ✅ Provides helpful usage examples and error messages
- ✅ Handles parameter validation
- ✅ Shows the actual Python command being executed

## 🔗 Handling Resource Dependencies

FHIR resources often reference other resources (e.g., Observations reference Patients). If your FHIR server validates references:

1. **Upload in dependency order**: Upload referenced resources first (Patients, Practitioners) before dependent resources (Observations, AllergyIntolerances)
2. **Multiple passes**: Run the upload script multiple times - previously failed resources may succeed once their dependencies exist
3. **Server idempotency**: Ensure your FHIR server handles duplicate submissions gracefully

### Recommended Upload Order

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