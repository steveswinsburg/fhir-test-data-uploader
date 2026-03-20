import os
import argparse
import requests
import json
from requests.auth import HTTPBasicAuth

DEBUG = False

def build_dependency_map(directory, resource_type_filter=None):
    """
    Scan all JSON files in the directory, extract resourceType, id, and references.
    Returns a dict: {filename: {resourceType, id, references, loaded}}
    """
    dependency_map = {}
    files = sorted(f for f in os.listdir(directory)
                   if f.endswith(".json") and (resource_type_filter is None or f.startswith(f"{resource_type_filter}-")))
    for filename in files:
        filepath = os.path.join(directory, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as file:
                json_data = json.load(file)
        except Exception as e:
            print(f"[ERROR] Failed to parse {filename}: {e}")
            continue

        resource_type = json_data.get('resourceType')
        resource_id = json_data.get('id')
        # Fallback to filename parsing if id/resourceType missing
        filename_without_ext = filename.rsplit(".", 1)[0]
        if not resource_type or not resource_id:
            if "-" in filename_without_ext:
                ft, fid = filename_without_ext.split("-", 1)
                if not resource_type:
                    resource_type = ft
                if not resource_id:
                    resource_id = fid

        # Find all references in the JSON (recursive search)
        references = []
        def find_references(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "reference" and isinstance(v, str):
                        references.append(v)
                    else:
                        find_references(v)
            elif isinstance(obj, list):
                for item in obj:
                    find_references(item)
        find_references(json_data)

        dependency_map[filename] = {
            "resourceType": resource_type,
            "id": resource_id,
            "references": references,
            "loaded": False
        }

    if(DEBUG):
        print(f"[DEBUG] Built dependency map: {json.dumps(dependency_map, indent=2)}\n")

    return dependency_map

def topological_sort_files(dependency_map):
    """
    Returns a list of filenames sorted so dependencies (references) come first.
    """
    # Build a map from (resourceType, id) to filename
    id_to_file = {}
    for fname, meta in dependency_map.items():
        if meta["resourceType"] and meta["id"]:
            id_to_file[(meta["resourceType"], meta["id"])] = fname

    # Build adjacency list: file -> set of files it depends on
    adj = {fname: set() for fname in dependency_map}
    for fname, meta in dependency_map.items():
        for ref in meta["references"]:
            # Only handle local references (ResourceType/id)
            if "/" in ref:
                ref_type, ref_id = ref.split("/", 1)
                dep_fname = id_to_file.get((ref_type, ref_id))
                if dep_fname:
                    adj[fname].add(dep_fname)

    # Kahn's algorithm for topological sort
    from collections import deque
    in_degree = {fname: 0 for fname in dependency_map}
    for deps in adj.values():
        for dep in deps:
            in_degree[dep] += 1

    queue = deque([fname for fname, deg in in_degree.items() if deg == 0])
    sorted_files = []
    while queue:
        fname = queue.popleft()
        sorted_files.append(fname)
        for dep in adj[fname]:
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)

    if len(sorted_files) != len(dependency_map):
        print("[WARNING] Cycle detected or missing references; some files may not be sorted correctly.")
        # Add remaining files in any order
        remaining = set(dependency_map) - set(sorted_files)
        sorted_files.extend(remaining)

    sorted = list(reversed(sorted_files))  # Reverse so dependencies come first

    if(DEBUG):
        print(f"[DEBUG] Topologically sorted files: {sorted}\n")
    
    return sorted


def put_files(base_url, directory, auth=None, resource_type_filter=None):
    print("Building dependency map... \n")
    dependency_map = build_dependency_map(directory, resource_type_filter)

    print("Topologically sorting files... \n")
    sorted_files = topological_sort_files(dependency_map)

    if not sorted_files:
        print(f"[INFO] No files found for resource type: {resource_type_filter}")
        return

    print("Uploading files...\n")
    for filename in sorted_files:
        meta = dependency_map[filename]
        if meta["loaded"]:
            continue
        if(DEBUG):
            print(f"[DEBUG] Handling {filename}")
        filepath = os.path.join(directory, filename)
        resource_type = meta["resourceType"]
        resource_id = meta["id"]
        if not resource_type or not resource_id:
            print(f"[WARNING] Skipping {filename}: Missing resourceType or id")
            continue
        url = f"{base_url}/{resource_type}/{resource_id}"
        try:
            with open(filepath, 'r', encoding='utf-8') as file:
                json_data = json.load(file)
        except Exception as e:
            print(f"[ERROR] Failed to parse {filename}: {e}")
            continue
        # Ensure ID and resourceType are correct
        if json_data.get('id') != resource_id:
            json_data['id'] = resource_id
        if json_data.get('resourceType') != resource_type:
            json_data['resourceType'] = resource_type
        try:
            response = requests.put(
                url,
                json=json_data,
                headers={"Content-Type": "application/fhir+json"},
                auth=auth
            )
            print(f"[INFO] PUT {filename} to {url} - Status: {response.status_code}")
            if response.ok:
                dependency_map[filename]["loaded"] = True
            else:
                print(f"[ERROR] Response: {response.text}")
        except Exception as e:
            print(f"[ERROR] Failed to PUT {filename}: {e}")

    print("\nUpload process completed.")


def main():
    parser = argparse.ArgumentParser(description="Upload FHIR JSON files to a server.")
    parser.add_argument('--data', required=True, help='Directory containing FHIR JSON files')
    parser.add_argument('--host', required=True, help='Base URL of the FHIR server including /fhir (e.g. https://example.com/fhir)')
    parser.add_argument('--user', help='Username for basic auth')
    parser.add_argument('--password', help='Password for basic auth')
    parser.add_argument('--type', help='Optional FHIR resource type to filter uploads (e.g. AllergyIntolerance)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug

    auth = HTTPBasicAuth(args.user, args.password) if args.user and args.password else None
    put_files(args.host.rstrip("/"), args.data, auth, args.type)

if __name__ == "__main__":
    main()