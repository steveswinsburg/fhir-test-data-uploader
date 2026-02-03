#!/bin/bash

# Exit on any error
set -e

# Default values
DATA=""
HOST=""
USER=""
PASSWORD=""
TYPE=""

# Parse named parameters
while [[ $# -gt 0 ]]; do
  key="$1"
  case $key in
    --data)
      DATA="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --user)
      USER="$2"
      shift 2
      ;;
    --password)
      PASSWORD="$2"
      shift 2
      ;;
    --type)
      TYPE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 --data <directory> --host <fhir-server-url> [--user <username>] [--password <password>] [--type <resource-type>]"
      exit 1
      ;;
  esac
done

# Check required parameters
if [ -z "$DATA" ] || [ -z "$HOST" ]; then
  echo "Usage: $0 --data <directory> --host <fhir-server-url> [--user <username>] [--password <password>] [--type <resource-type>]"
  echo ""
  echo "Required:"
  echo "  --data      Directory containing FHIR JSON files"
  echo "  --host      Base URL of the FHIR server including /fhir (e.g. http://localhost:8080/fhir)"
  echo ""
  echo "Optional:"
  echo "  --user      Username for basic auth"
  echo "  --password  Password for basic auth"
  echo "  --type      FHIR resource type to filter uploads (e.g. Patient, AllergyIntolerance)"
  echo ""
  echo "Examples:"
  echo "  $0 --data ./test-data --host http://localhost:8080/fhir"
  echo "  $0 --data ./test-data --host https://fhir.example.com/fhir --user admin --password secret"
  echo "  $0 --data ./test-data --host http://localhost:8080/fhir --type Patient"
  exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv venv
fi

# Activate the virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Clear pip cache to avoid deserialization issues
echo "Clearing pip cache..."
pip3 cache purge --quiet 2>/dev/null || true

# Upgrade pip to latest version (suppress warnings)
echo "Upgrading pip to latest version..."
pip3 install --upgrade pip --quiet

# Install required packages
echo "Installing dependencies..."
pip3 install -r requirements.txt

# Build the python command with arguments
PYTHON_CMD="python3 upload.py --data \"$DATA\" --host \"$HOST\""

if [ ! -z "$USER" ]; then
  PYTHON_CMD="$PYTHON_CMD --user \"$USER\""
fi

if [ ! -z "$PASSWORD" ]; then
  PYTHON_CMD="$PYTHON_CMD --password \"$PASSWORD\""
fi

if [ ! -z "$TYPE" ]; then
  PYTHON_CMD="$PYTHON_CMD --type \"$TYPE\""
fi

echo "Uploading FHIR data..."
echo "Command: $PYTHON_CMD"
echo ""

# Run the upload script
eval $PYTHON_CMD