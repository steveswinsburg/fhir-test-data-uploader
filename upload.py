import os
import argparse
import requests
import json
from requests.auth import HTTPBasicAuth
from collections import deque

DEBUG = False
NDJSON_PROGRESS_INTERVAL = 1000
TYPE_ORDER_SAMPLE_SIZE = 5


def matches_resource_type_filter(filename, resource_type_filter=None):
    if resource_type_filter is None:
        return True

    filename_without_ext = os.path.splitext(filename)[0]
    return filename_without_ext == resource_type_filter or filename_without_ext.startswith(f"{resource_type_filter}-")


def list_fhir_files(directory, resource_type_filter=None, extensions=None):
    if extensions is None:
        extensions = {".json", ".ndjson"}

    return sorted(
        f for f in os.listdir(directory)
        if os.path.splitext(f)[1].lower() in extensions and matches_resource_type_filter(f, resource_type_filter)
    )


def derive_resource_type_and_id_from_filename(filename):
    filename_without_ext = os.path.splitext(filename)[0]
    if "-" not in filename_without_ext:
        return filename_without_ext, None

    return filename_without_ext.split("-", 1)


def extract_references(json_data):
    references = []

    def find_references(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "reference" and isinstance(value, str):
                    references.append(value)
                else:
                    find_references(value)
        elif isinstance(obj, list):
            for item in obj:
                find_references(item)

    find_references(json_data)
    return references


def parse_reference_type(reference):
    if not reference or reference.startswith("#"):
        return None

    normalized_reference = reference.split("/_history/", 1)[0].rstrip("/")
    parts = [part for part in normalized_reference.split("/") if part]
    if len(parts) < 2:
        return None

    resource_type = parts[-2]
    if not resource_type or not resource_type[0].isalpha():
        return None

    return resource_type


def upload_resource(base_url, json_data, auth=None, source_label=None):
    resource_type = json_data.get("resourceType")
    resource_id = json_data.get("id")

    if not resource_type or not resource_id:
        print(f"⚠️ [WARNING] Skipping {source_label or 'resource'}: Missing resourceType or id")
        return False

    url = f"{base_url}/{resource_type}/{resource_id}"
    try:
        response = requests.put(
            url,
            json=json_data,
            headers={"Content-Type": "application/fhir+json"},
            auth=auth
        )
    except Exception as e:
        print(f"❌ [ERROR] Failed to PUT {source_label or f'{resource_type}/{resource_id}'}: {e}")
        return False

    if DEBUG:
        print(f"✅ [INFO] PUT {source_label or f'{resource_type}/{resource_id}'} to {url} - Status: {response.status_code}")

    if not response.ok:
        print(f"❌ [ERROR] Response for {source_label or f'{resource_type}/{resource_id}'}: {response.text}")

    return response.ok

def build_dependency_map(directory, resource_type_filter=None):
    """
    Scan all JSON files in the directory, extract resourceType, id, and references.
    Returns a dict: {filename: {resourceType, id, references, loaded}}
    """
    dependency_map = {}
    files = list_fhir_files(directory, resource_type_filter, extensions={".json"})
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
        if not resource_type or not resource_id:
            ft, fid = derive_resource_type_and_id_from_filename(filename)
            if not resource_type:
                resource_type = ft
            if not resource_id:
                resource_id = fid

        # Ensure the parsed data matches the resolved type and id
        json_data['resourceType'] = resource_type
        json_data['id'] = resource_id

        references = extract_references(json_data)

        dependency_map[filename] = {
            "resourceType": resource_type,
            "id": resource_id,
            "references": references,
            "json_data": json_data,
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


def sample_resources_from_file(filepath, sample_size=TYPE_ORDER_SAMPLE_SIZE):
    extension = os.path.splitext(filepath)[1].lower()
    sampled_resources = []

    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            if extension == ".ndjson":
                for line_number, line in enumerate(file, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sampled_resources.append(json.loads(line))
                    except Exception as e:
                        print(f"❌ [ERROR] Failed to parse sample from {os.path.basename(filepath)}:{line_number}: {e}")
                    if len(sampled_resources) >= sample_size:
                        break
            else:
                sampled_resources.append(json.load(file))
    except Exception as e:
        print(f"❌ [ERROR] Failed to sample {os.path.basename(filepath)}: {e}")

    return sampled_resources


def infer_resource_types_for_file(filename, sampled_resources):
    inferred_types = []
    for resource in sampled_resources:
        resource_type = resource.get("resourceType")
        if resource_type and resource_type not in inferred_types:
            inferred_types.append(resource_type)

    if inferred_types:
        return inferred_types

    inferred_type, _ = derive_resource_type_and_id_from_filename(filename)
    return [inferred_type] if inferred_type else []


def infer_type_order(directory, sample_size=TYPE_ORDER_SAMPLE_SIZE):
    files = list_fhir_files(directory)
    if not files:
        return []

    type_dependencies = {}
    all_types = []

    print(f"🧭 Sampling up to {sample_size} resources per file to infer resource type order...\n")
    for filename in files:
        filepath = os.path.join(directory, filename)
        sampled_resources = sample_resources_from_file(filepath, sample_size=sample_size)
        inferred_types = infer_resource_types_for_file(filename, sampled_resources)
        if not inferred_types:
            continue

        for resource_type in inferred_types:
            if resource_type not in type_dependencies:
                type_dependencies[resource_type] = set()
            if resource_type not in all_types:
                all_types.append(resource_type)

        for resource in sampled_resources:
            source_type = resource.get("resourceType")
            if not source_type:
                continue
            source_dependencies = type_dependencies.setdefault(source_type, set())
            for reference in extract_references(resource):
                dependency_type = parse_reference_type(reference)
                if dependency_type and dependency_type != source_type:
                    source_dependencies.add(dependency_type)

    known_types = set(all_types)
    for resource_type in list(type_dependencies.keys()):
        type_dependencies[resource_type] = {
            dependency_type for dependency_type in type_dependencies[resource_type]
            if dependency_type in known_types
        }

    if not type_dependencies:
        return []

    adjacency = {resource_type: set() for resource_type in type_dependencies}
    in_degree = {resource_type: 0 for resource_type in type_dependencies}
    for resource_type, dependencies in type_dependencies.items():
        for dependency_type in dependencies:
            adjacency.setdefault(dependency_type, set()).add(resource_type)
            in_degree[resource_type] += 1

    queue = deque(sorted(resource_type for resource_type, degree in in_degree.items() if degree == 0))
    ordered_types = []
    while queue:
        resource_type = queue.popleft()
        ordered_types.append(resource_type)
        for dependent_type in sorted(adjacency.get(resource_type, set())):
            in_degree[dependent_type] -= 1
            if in_degree[dependent_type] == 0:
                queue.append(dependent_type)

    if len(ordered_types) != len(type_dependencies):
        remaining_types = sorted(set(type_dependencies) - set(ordered_types))
        print(
            "[WARNING] Could not fully infer type order from sampled references; "
            f"appending remaining resource types alphabetically: {', '.join(remaining_types)}"
        )
        ordered_types.extend(remaining_types)

    print("[INFO] Inferred resource type order:")
    for index, resource_type in enumerate(ordered_types, start=1):
        dependencies = sorted(type_dependencies.get(resource_type, set()))
        dependency_summary = ", ".join(dependencies) if dependencies else "none sampled"
        print(f"  {index}. {resource_type} (depends on: {dependency_summary})")
    print("")

    return ordered_types


def put_json_files(base_url, directory, auth=None, resource_type_filter=None):
    json_files = list_fhir_files(directory, resource_type_filter, extensions={".json"})
    if not json_files:
        return 0

    print("🛠️  Building dependency map... \n")
    dependency_map = build_dependency_map(directory, resource_type_filter)
    if not dependency_map:
        return 0

    print("🔀 Topologically sorting files... \n")
    sorted_files = topological_sort_files(dependency_map)

    if not sorted_files:
        return 0

    print("⬆️  Uploading JSON files...\n")
    uploaded_count = 0
    for filename in sorted_files:
        meta = dependency_map[filename]
        if meta["loaded"]:
            continue
        if DEBUG:
            print(f"[DEBUG] Handling {filename}")
        resource_type = meta["resourceType"]
        resource_id = meta["id"]
        if not resource_type or not resource_id:
            print(f"⚠️ [WARNING] Skipping {filename}: Missing resourceType or id")
            continue
        if upload_resource(base_url, meta["json_data"], auth=auth, source_label=filename):
            dependency_map[filename]["loaded"] = True
            uploaded_count += 1

    return uploaded_count


def put_ndjson_file(base_url, filepath, auth=None, resource_type_filter=None):
    filename = os.path.basename(filepath)
    processed = 0
    uploaded = 0
    skipped = 0
    failed = 0

    print(f"⬆️  Uploading NDJSON file: {filename}\n")
    with open(filepath, 'r', encoding='utf-8') as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue

            processed += 1
            try:
                json_data = json.loads(line)
            except Exception as e:
                failed += 1
                print(f"❌ [ERROR] Failed to parse {filename}:{line_number}: {e}")
                continue

            resource_type = json_data.get("resourceType")
            if resource_type_filter and resource_type != resource_type_filter:
                skipped += 1
                continue

            source_label = f"{filename}:{line_number}"
            if upload_resource(base_url, json_data, auth=auth, source_label=source_label):
                uploaded += 1
            else:
                failed += 1

            if not DEBUG and processed % NDJSON_PROGRESS_INTERVAL == 0:
                print(
                    f"[INFO] {filename}: processed={processed}, uploaded={uploaded}, failed={failed}, skipped={skipped}"
                )

    print(
        f"[INFO] Completed {filename}: processed={processed}, uploaded={uploaded}, failed={failed}, skipped={skipped}\n"
    )
    return uploaded


def put_ndjson_files(base_url, directory, auth=None, resource_type_filter=None):
    ndjson_files = list_fhir_files(directory, resource_type_filter, extensions={".ndjson"})
    uploaded_count = 0
    for filename in ndjson_files:
        filepath = os.path.join(directory, filename)
        uploaded_count += put_ndjson_file(base_url, filepath, auth=auth, resource_type_filter=resource_type_filter)
    return uploaded_count


def put_files_for_resource_type(base_url, directory, resource_type, auth=None):
    print(f"📦 Processing resource type: {resource_type}\n")
    matching_files = list_fhir_files(directory, resource_type)
    if not matching_files:
        print(f"[INFO] No files found for resource type: {resource_type}\n")
        return

    uploaded_count = 0
    uploaded_count += put_json_files(base_url, directory, auth=auth, resource_type_filter=resource_type)
    uploaded_count += put_ndjson_files(base_url, directory, auth=auth, resource_type_filter=resource_type)


def put_files_in_order(base_url, directory, type_order, auth=None):
    print(f"📚 Uploading resources in explicit order: {', '.join(type_order)}\n")
    for resource_type in type_order:
        put_files_for_resource_type(base_url, directory, resource_type, auth=auth)


def put_files(base_url, directory, auth=None, resource_type_filter=None):
    matching_files = list_fhir_files(directory, resource_type_filter)
    if not matching_files:
        print(f"[INFO] No files found for resource type: {resource_type_filter}")
        return

    if resource_type_filter is None:
        ndjson_files = list_fhir_files(directory, extensions={".ndjson"})
        if ndjson_files:
            inferred_type_order = infer_type_order(directory)
            if inferred_type_order:
                put_files_in_order(base_url, directory, inferred_type_order, auth=auth)
                print("\n🎉 Upload process complete.\n")
                return

    json_uploaded = put_json_files(base_url, directory, auth=auth, resource_type_filter=resource_type_filter)
    ndjson_uploaded = put_ndjson_files(base_url, directory, auth=auth, resource_type_filter=resource_type_filter)

    print("\n🎉 Upload process complete.\n")

def main():
    parser = argparse.ArgumentParser(description="Upload FHIR JSON files to a server.")
    parser.add_argument('--data', required=True, help='Directory containing FHIR JSON files')
    parser.add_argument('--host', required=True, help='Base URL of the FHIR server including /fhir (e.g. https://example.com/fhir)')
    parser.add_argument('--user', help='Username for basic auth')
    parser.add_argument('--password', help='Password for basic auth')
    parser.add_argument('--type', help='Optional FHIR resource type to filter uploads (e.g. AllergyIntolerance)')
    parser.add_argument('--type-order', help='Comma-separated resource types to upload in order (e.g. Patient,Practitioner,Observation)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()

    if args.type and args.type_order:
        parser.error('--type and --type-order cannot be used together')

    global DEBUG
    DEBUG = args.debug

    auth = HTTPBasicAuth(args.user, args.password) if args.user and args.password else None
    if args.type_order:
        type_order = [resource_type.strip() for resource_type in args.type_order.split(',') if resource_type.strip()]
        if not type_order:
            parser.error('--type-order must contain at least one resource type')
        put_files_in_order(args.host.rstrip("/"), args.data, type_order, auth)
    else:
        put_files(args.host.rstrip("/"), args.data, auth, args.type)

if __name__ == "__main__":
    main()