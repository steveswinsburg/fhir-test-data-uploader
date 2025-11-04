import os
import argparse
import requests
import json
from requests.auth import HTTPBasicAuth

def put_files(base_url, directory, auth=None, resource_type_filter=None):
    files = sorted(f for f in os.listdir(directory)
                   if f.endswith(".json") and (resource_type_filter is None or f.startswith(f"{resource_type_filter}-")))

    if not files:
        print(f"[INFO] No files found for resource type: {resource_type_filter}")
        return

    for filename in files:
        filepath = os.path.join(directory, filename)
        # Extract resource type and ID from filename
        # Example: Patient-dietrich-kimbra-althea.json -> type=Patient, id=dietrich-kimbra-althea
        filename_without_ext = filename.rsplit(".", 1)[0]  # Remove .json extension
        
        if "-" not in filename_without_ext:
            print(f"[WARNING] Skipping {filename}: No dash found in filename")
            continue
            
        resource_type, resource_id = filename_without_ext.split("-", 1)  # Split on first dash only
            
        url = f"{base_url}/{resource_type}/{resource_id}"

        with open(filepath, 'r', encoding='utf-8') as file:
            json_data = json.load(file)
        
        # Check if the JSON already has an ID
        existing_id = json_data.get('id')
        if existing_id:
            if existing_id != resource_id:
                print(f"[WARNING] {filename}: JSON ID '{existing_id}' doesn't match filename ID '{resource_id}'. Using filename ID.")
                json_data['id'] = resource_id
        else:
            print(f"[INFO] {filename}: No ID in JSON, setting to '{resource_id}'")
            json_data['id'] = resource_id
        
        # Also ensure the resourceType matches (just in case)
        if json_data.get('resourceType') != resource_type:
            print(f"[WARNING] {filename}: JSON resourceType '{json_data.get('resourceType')}' doesn't match filename type '{resource_type}'. Using filename type.")
            json_data['resourceType'] = resource_type

        try:
            response = requests.put(
                url,
                json=json_data,  # Use json parameter instead of data
                headers={"Content-Type": "application/fhir+json"},
                auth=auth
            )
            print(f"PUT {filename} to {url} - Status: {response.status_code}")
            if not response.ok:
                print(f"  Response: {response.text}")
        except Exception as e:
            print(f"Failed to PUT {filename}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Upload FHIR JSON files to a server.")
    parser.add_argument('--data', required=True, help='Directory containing FHIR JSON files')
    parser.add_argument('--host', required=True, help='Base URL of the FHIR server including /fhir (e.g. https://example.com/fhir)')
    parser.add_argument('--user', help='Username for basic auth')
    parser.add_argument('--password', help='Password for basic auth')
    parser.add_argument('--type', help='Optional FHIR resource type to filter uploads (e.g. AllergyIntolerance)')
    args = parser.parse_args()

    auth = HTTPBasicAuth(args.user, args.password) if args.user and args.password else None
    put_files(args.host.rstrip("/"), args.data, auth, args.type)

if __name__ == "__main__":
    main()