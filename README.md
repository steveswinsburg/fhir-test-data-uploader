# FHIR Test Data Uploader

A script to upload FHIR resources to a FHIR server

## Prerequisites
Python 3

## Installation (in venv)

```
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
```

## Requirements

1. A set of FHIR resources of the format ResourceType-whatever.json where ResourceType is the actual FHIR resource type, e.g., AllergyIntolerance
2. A FHIR server with Basic Auth (currently the only supported option for writing data)

## Running

`python upload.py --data ./fhir-data --host https://your.fhir.server/fhir --user yourusername --password yourpassword`

## Note
Linkages between resources may cause failures if the linked resource doesn't exist first and your FHIR server performs validation.
Running the full set of resources again should be resolve the linked resources - hopefully the existing ones will be rejected, and the new ones added.
Ensure your FHIR server is idempotent :)