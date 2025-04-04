import os
import argparse
import requests
from requests.auth import HTTPBasicAuth

def post_files(base_url, directory, auth=None):
    files = sorted(f for f in os.listdir(directory) if f.endswith(".json"))

    for filename in files:
        filepath = os.path.join(directory, filename)
        resource_type = filename.split("-")[0]
        url = f"{base_url}/{resource_type}"

        with open(filepath, 'r', encoding='utf-8') as file:
            json_data = file.read()

        try:
            response = requests.post(
                url,
                data=json_data,
                headers={"Content-Type": "application/fhir+json"},
                auth=auth
            )
            print(f"Posted {filename} to {url} - Status: {response.status_code}")
            if not response.ok:
                print(f"  Response: {response.text}")
        except Exception as e:
            print(f"Failed to post {filename}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Upload FHIR JSON files to a server.")
    parser.add_argument('--data', required=True, help='Directory containing FHIR JSON files')
    parser.add_argument('--host', required=True, help='Base URL of the FHIR server including /fhir (e.g. https://example.com/fhir)')
    parser.add_argument('--user', help='Username for basic auth')
    parser.add_argument('--password', help='Password for basic auth')
    args = parser.parse_args()

    auth = HTTPBasicAuth(args.user, args.password) if args.user and args.password else None
    post_files(args.host.rstrip("/"), args.data, auth)

if __name__ == "__main__":
    main()