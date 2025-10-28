# 🏥 FHIR Test Data Uploader

A Python utility for uploading FHIR JSON files to FHIR servers. This tool is designed to help you quickly populate FHIR servers with test data for development, testing, and demonstration purposes.

## ✨ Features

- 📁 Batch upload multiple FHIR JSON files
- 🔒 Support for Basic Authentication
- 🎯 Optional filtering by FHIR resource type
- 📊 Progress reporting with status codes
- 🔄 Automatic resource type detection from filename
- 🛡️ Error handling and detailed logging

## 📋 Prerequisites

- **Python 3.6+**
- **pip** (Python package installer)
- Access to a FHIR R4 compliant server

## 🚀 Installation

### Option 1: Using Virtual Environment (Recommended)

```bash
# Clone or download this repository
git clone https://github.com/steveswinsburg/fhir-test-data-uploader.git
cd fhir-test-data-uploader

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Option 2: System-wide Installation

```bash
pip install requests
```

## 📁 Data Format Requirements

Your FHIR JSON files must follow this naming convention:
```
ResourceType-identifier.json
```

**Examples:**
- `Patient-john-doe.json`
- `AllergyIntolerance-peanut-allergy.json`
- `Observation-blood-pressure-001.json`
- `Practitioner-dr-smith.json`

The script automatically extracts the resource type from the filename prefix and posts to the appropriate FHIR endpoint.

## 🏃‍♂️ Usage

### Basic Usage

```bash
python upload.py --data ./fhir-data --host https://your.fhir.server/fhir
```

### With Authentication

```bash
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --user yourusername \
  --password yourpassword
```

### Upload Specific Resource Type

```bash
python upload.py \
  --data ./fhir-data \
  --host https://your.fhir.server/fhir \
  --user yourusername \
  --password yourpassword \
  --type Patient
```

### Command Line Arguments

| Argument | Required | Description | Example |
|----------|----------|-------------|---------|
| `--data` | ✅ | Directory containing FHIR JSON files | `./fhir-data` |
| `--host` | ✅ | Base URL of FHIR server (include `/fhir`) | `https://hapi.fhir.org/baseR4` |
| `--user` | ❌ | Username for Basic Authentication | `admin` |
| `--password` | ❌ | Password for Basic Authentication | `password123` |
| `--type` | ❌ | Filter uploads to specific resource type | `Patient` |

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

- **Data Privacy**: Never upload real patient data to public test servers
- **Rate Limiting**: Some servers may have rate limits - add delays if needed
- **Authentication**: Currently supports HTTP Basic Auth only
- **FHIR Version**: Designed for FHIR R4
- **Content Type**: Uses `application/fhir+json` header

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