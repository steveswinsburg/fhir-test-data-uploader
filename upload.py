import os
import re
import sys
import argparse
import requests
import json
import random
import time
import threading
import concurrent.futures
from datetime import datetime
from requests.auth import HTTPBasicAuth
from collections import deque

DEBUG = False
NDJSON_PROGRESS_INTERVAL = 1000
TYPE_ORDER_SAMPLE_SIZE = 5
TYPE_ORDER_SCAN_MODE = "full"
EXIT_CODE_INCOMPLETE = 2
TRANSIENT_DB_CONFLICT_MAX_RETRIES = 4
TRANSIENT_DB_CONFLICT_BASE_DELAY_SECONDS = 0.5
# Substrings seen in HAPI FHIR OperationOutcome diagnostics when the underlying database
# aborted the write due to transient write/write contention rather than a real data problem.
# These are safe to retry as-is since the original write was rolled back, not partially applied.
TRANSIENT_DB_CONFLICT_MARKERS = (
    "deadlock detected",
    "LockAcquisitionException",
    "could not serialize access",
    "CannotAcquireLockException",
)


class ChunkLimitReached(Exception):
    """Raised once --chunk-size resources have been processed in this run; progress has already been saved."""
    pass


class UploadTimer:
    """
    Tracks wall-clock time actually spent sending/receiving upload requests, separate from
    setup work (building dependency maps, topologically sorting files, inferring NDJSON type
    order) that runs before the first resource is ever uploaded. That setup phase can take a
    noticeable amount of time on large datasets, and including it in the reported records/sec
    rate understates real upload throughput.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.first_request_at = None
        self.last_response_at = None

    def record(self, start, end):
        with self._lock:
            if self.first_request_at is None or start < self.first_request_at:
                self.first_request_at = start
            if self.last_response_at is None or end > self.last_response_at:
                self.last_response_at = end

    def elapsed_seconds(self):
        if self.first_request_at is None or self.last_response_at is None:
            return 0.0
        return self.last_response_at - self.first_request_at


UPLOAD_TIMER = UploadTimer()


class ProgressTracker:
    """Tracks which files/resources have been uploaded so large uploads can be chunked and resumed."""

    def __init__(self, progress_file, chunk_size=None):
        self.progress_file = progress_file
        self.chunk_size = chunk_size
        self.processed_this_run = 0
        self.uploaded_this_run = 0
        self.data = self._load()

    def _load(self):
        if self.progress_file and os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                data.setdefault("files", {})
                data.setdefault("total_uploaded", 0)
                data.setdefault("total_processed", 0)
                data.setdefault("completed", False)
                return data
            except Exception as e:
                print(f"⚠️ [WARNING] Failed to read progress file {self.progress_file}: {e}. Starting fresh.\n")
        return {"files": {}, "total_uploaded": 0, "total_processed": 0, "completed": False}

    def save(self):
        if not self.progress_file:
            return
        directory = os.path.dirname(self.progress_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_path = f"{self.progress_file}.tmp"
        with open(tmp_path, 'w', encoding='utf-8') as file:
            json.dump(self.data, file, indent=2)
        os.replace(tmp_path, self.progress_file)

    def reset(self):
        self.data = {"files": {}, "total_uploaded": 0, "total_processed": 0, "completed": False}
        self.processed_this_run = 0
        self.uploaded_this_run = 0
        self.save()

    def is_file_done(self, filename):
        return self.data["files"].get(filename, {}).get("done", False)

    def mark_file_done(self, filename):
        self.data["files"].setdefault(filename, {})["done"] = True

    def get_ndjson_resume_count(self, filename):
        return self.data["files"].get(filename, {}).get("processed_resources", 0)

    def set_ndjson_position(self, filename, processed_resources):
        self.data["files"].setdefault(filename, {})["processed_resources"] = processed_resources

    def record_result(self, uploaded):
        self.processed_this_run += 1
        self.data["total_processed"] += 1
        if uploaded:
            self.data["total_uploaded"] += 1
            self.uploaded_this_run += 1

    def should_checkpoint(self):
        return self.data["total_processed"] % NDJSON_PROGRESS_INTERVAL == 0

    def chunk_reached(self):
        return self.chunk_size is not None and self.processed_this_run >= self.chunk_size


def default_progress_file(data_dir, host):
    sanitized_host = re.sub(r'[^A-Za-z0-9]+', '_', host).strip('_')
    return os.path.join(data_dir, f".upload-progress.{sanitized_host}.json")


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
        if not f.startswith(".")
        and os.path.splitext(f)[1].lower() in extensions
        and matches_resource_type_filter(f, resource_type_filter)
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


def is_transient_db_conflict(response_text):
    """
    True when a failed response looks like a transient database write/write conflict
    (e.g. a Postgres deadlock between two concurrent inserts) rather than a real data
    or validation problem. The original write was rolled back by the database in this
    case, so retrying the identical PUT is safe.
    """
    return any(marker in response_text for marker in TRANSIENT_DB_CONFLICT_MARKERS)


def upload_resource(base_url, json_data, session, source_label=None):
    resource_type = json_data.get("resourceType")
    resource_id = json_data.get("id")

    if not resource_type or not resource_id:
        print(f"⚠️ [WARNING] Skipping {source_label or 'resource'}: Missing resourceType or id")
        return False

    url = f"{base_url}/{resource_type}/{resource_id}"
    label = source_label or f"{resource_type}/{resource_id}"

    attempt = 0
    while True:
        request_start = time.perf_counter()
        try:
            # Using the shared session keeps the underlying TCP/TLS connection alive across
            # requests (HTTP keep-alive) instead of reconnecting for every single resource,
            # which otherwise dominates per-record latency on high-RTT/VPN network paths.
            response = session.put(
                url,
                json=json_data,
                headers={"Content-Type": "application/fhir+json"},
            )
        except Exception as e:
            print(f"❌ [ERROR] Failed to PUT {label}: {e}")
            return False
        finally:
            UPLOAD_TIMER.record(request_start, time.perf_counter())

        if DEBUG:
            print(f"✅ [INFO] PUT {label} to {url} - Status: {response.status_code}")

        if response.ok:
            return True

        if attempt < TRANSIENT_DB_CONFLICT_MAX_RETRIES and is_transient_db_conflict(response.text):
            delay = TRANSIENT_DB_CONFLICT_BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 0.25)
            attempt += 1
            print(
                f"⚠️  [RETRY] Transient database conflict for {label} "
                f"(attempt {attempt}/{TRANSIENT_DB_CONFLICT_MAX_RETRIES}); retrying in {delay:.1f}s...\n"
            )
            time.sleep(delay)
            continue

        print(f"❌ [ERROR] Response for {label}: {response.text}")
        return False


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

    # Build adjacency list: dependency file -> set of files that depend on it
    adj = {fname: set() for fname in dependency_map}
    in_degree = {fname: 0 for fname in dependency_map}

    for fname, meta in dependency_map.items():
        for ref in meta["references"]:
            # Only handle local references (ResourceType/id)
            if "/" in ref:
                ref_type, ref_id = ref.split("/", 1)
                dep_fname = id_to_file.get((ref_type, ref_id))
                if dep_fname:
                    # dep_fname must be uploaded before fname
                    if fname not in adj[dep_fname]:
                        adj[dep_fname].add(fname)
                        in_degree[fname] += 1

    # Kahn's algorithm for topological sort (dependencies first)
    queue = deque(sorted(fname for fname, deg in in_degree.items() if deg == 0))
    sorted_files = []
    while queue:
        fname = queue.popleft()
        sorted_files.append(fname)
        for dependent in sorted(adj[fname]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(sorted_files) != len(dependency_map):
        remaining = sorted(set(dependency_map) - set(sorted_files))
        print(
            "[WARNING] Cycle detected or missing references; files with unresolved dependencies: "
            + ", ".join(remaining)
        )
        # Add remaining files in any order
        sorted_files.extend(remaining)

    if(DEBUG):
        print(f"[DEBUG] Topologically sorted files: {sorted_files}\n")
    
    return sorted_files


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
                    if sample_size and sample_size > 0 and len(sampled_resources) >= sample_size:
                        break
            else:
                sampled_resources.append(json.load(file))
    except Exception as e:
        print(f"❌ [ERROR] Failed to sample {os.path.basename(filepath)}: {e}")

    return sampled_resources


def iter_resources_from_file(filepath, max_resources=None):
    extension = os.path.splitext(filepath)[1].lower()
    yielded = 0

    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            if extension == ".ndjson":
                for line_number, line in enumerate(file, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        resource = json.loads(line)
                    except Exception as e:
                        print(f"❌ [ERROR] Failed to parse sample from {os.path.basename(filepath)}:{line_number}: {e}")
                        continue

                    yield resource
                    yielded += 1
                    if max_resources and yielded >= max_resources:
                        return
            else:
                try:
                    resource = json.load(file)
                except Exception as e:
                    print(f"❌ [ERROR] Failed to sample {os.path.basename(filepath)}: {e}")
                    return

                yield resource
    except Exception as e:
        print(f"❌ [ERROR] Failed to sample {os.path.basename(filepath)}: {e}")


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

    if sample_size and sample_size > 0:
        print(f"🧭 Sampling up to {sample_size} resources per file to infer resource type order...\n")
    else:
        print("🧭 Scanning all resources per file to infer resource type order...\n")
    for filename in files:
        filepath = os.path.join(directory, filename)
        max_resources = sample_size if sample_size and sample_size > 0 else None
        inferred_types = []

        for resource in iter_resources_from_file(filepath, max_resources=max_resources):
            source_type = resource.get("resourceType")
            if not source_type:
                continue

            if source_type not in inferred_types:
                inferred_types.append(source_type)
            if source_type not in all_types:
                all_types.append(source_type)

            source_dependencies = type_dependencies.setdefault(source_type, set())
            for reference in extract_references(resource):
                dependency_type = parse_reference_type(reference)
                if dependency_type and dependency_type != source_type:
                    source_dependencies.add(dependency_type)

        if not inferred_types:
            inferred_types = infer_resource_types_for_file(filename, [])

        for resource_type in inferred_types:
            if resource_type not in type_dependencies:
                type_dependencies[resource_type] = set()
            if resource_type not in all_types:
                all_types.append(resource_type)

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


def put_json_files(base_url, directory, session, resource_type_filter=None, progress=None):
    json_files = list_fhir_files(directory, resource_type_filter, extensions={".json"})
    if not json_files:
        return 0

    print("🛠️  Building dependency map... \n")
    # Index all resources so references are available while sorting filtered uploads.
    dependency_map = build_dependency_map(directory)
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
        if resource_type_filter is not None and meta["resourceType"] != resource_type_filter:
            continue
        if progress and progress.is_file_done(filename):
            continue
        if DEBUG:
            print(f"[DEBUG] Handling {filename}")
        resource_type = meta["resourceType"]
        resource_id = meta["id"]
        if not resource_type or not resource_id:
            print(f"⚠️ [WARNING] Skipping {filename}: Missing resourceType or id")
            continue
        ok = upload_resource(base_url, meta["json_data"], session, source_label=filename)
        if ok:
            dependency_map[filename]["loaded"] = True
            uploaded_count += 1

        if progress:
            progress.record_result(ok)
            if ok:
                progress.mark_file_done(filename)
            if progress.should_checkpoint():
                progress.save()
            if progress.chunk_reached():
                progress.save()
                print(f"⏸️  Chunk size reached after {filename}. Progress saved to {progress.progress_file}.\n")
                raise ChunkLimitReached()

    return uploaded_count


def put_ndjson_file(base_url, filepath, session, resource_type_filter=None, progress=None):
    filename = os.path.basename(filepath)
    processed = 0
    uploaded = 0
    skipped = 0
    failed = 0
    resource_index = 0  # count of non-blank resources encountered, used as the resume marker
    resume_from = progress.get_ndjson_resume_count(filename) if progress else 0

    print(f"⬆️  Uploading NDJSON file: {filename}\n")
    if resume_from:
        print(f"⏭️  Resuming {filename} after previously processed resource #{resume_from}\n")

    with open(filepath, 'r', encoding='utf-8') as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue

            resource_index += 1
            if resource_index <= resume_from:
                continue

            processed += 1
            try:
                json_data = json.loads(line)
            except Exception as e:
                failed += 1
                print(f"❌ [ERROR] Failed to parse {filename}:{line_number}: {e}")
                if progress:
                    progress.set_ndjson_position(filename, resource_index)
                    progress.record_result(uploaded=False)
                    if progress.should_checkpoint():
                        progress.save()
                    if progress.chunk_reached():
                        progress.save()
                        print(f"⏸️  Chunk size reached while processing {filename} at resource #{resource_index}. Progress saved to {progress.progress_file}.\n")
                        raise ChunkLimitReached()
                continue

            resource_type = json_data.get("resourceType")
            if resource_type_filter and resource_type != resource_type_filter:
                skipped += 1
                if progress:
                    progress.set_ndjson_position(filename, resource_index)
                continue

            source_label = f"{filename}:{line_number}"
            ok = upload_resource(base_url, json_data, session, source_label=source_label)
            if ok:
                uploaded += 1
            else:
                failed += 1

            if progress:
                progress.set_ndjson_position(filename, resource_index)
                progress.record_result(ok)

            if not DEBUG and processed % NDJSON_PROGRESS_INTERVAL == 0:
                print(
                    f"[INFO] {filename}: processed={processed}, uploaded={uploaded}, failed={failed}, skipped={skipped}"
                )

            if progress:
                if progress.should_checkpoint():
                    progress.save()
                if progress.chunk_reached():
                    progress.save()
                    print(f"⏸️  Chunk size reached while processing {filename} at resource #{resource_index}. Progress saved to {progress.progress_file}.\n")
                    raise ChunkLimitReached()

    if progress:
        progress.mark_file_done(filename)

    print(
        f"[INFO] Completed {filename}: processed={processed}, uploaded={uploaded}, failed={failed}, skipped={skipped}\n"
    )
    return uploaded


def put_ndjson_files(base_url, directory, session, resource_type_filter=None, progress=None):
    ndjson_files = list_fhir_files(directory, resource_type_filter, extensions={".ndjson"})
    uploaded_count = 0
    for filename in ndjson_files:
        if progress and progress.is_file_done(filename):
            print(f"⏭️  Skipping {filename} (already fully uploaded)\n")
            continue
        filepath = os.path.join(directory, filename)
        uploaded_count += put_ndjson_file(base_url, filepath, session, resource_type_filter=resource_type_filter, progress=progress)
    return uploaded_count


def ndjson_type_has_self_references(directory, resource_type):
    """
    Scan every NDJSON resource of resource_type for a reference pointing to another
    resource of the SAME type (e.g. Organization.partOf, Location.partOf,
    Observation.partOf/hasMember). If any are found, upload order between individual
    resources of this type matters and concurrent upload cannot be proven safe, so the
    caller should fall back to sequential processing for this type.
    """
    ndjson_files = list_fhir_files(directory, resource_type, extensions={".ndjson"})
    for filename in ndjson_files:
        filepath = os.path.join(directory, filename)
        for resource in iter_resources_from_file(filepath):
            if resource.get("resourceType") != resource_type:
                continue
            for reference in extract_references(resource):
                if parse_reference_type(reference) == resource_type:
                    return True
    return False


def collect_ndjson_resources_for_type(directory, resource_type, progress=None):
    """
    Flatten every not-yet-uploaded NDJSON resource of resource_type (across all of its
    NDJSON files) into a list of (resource_key, json_data) tuples, ready for concurrent
    dispatch. resource_key is used both as a log label and as the resumability marker
    (tracked by individual resource rather than by file position, since concurrent
    uploads complete out of order).
    """
    ndjson_files = list_fhir_files(directory, resource_type, extensions={".ndjson"})
    completed = set(progress.data.get("completed_resources", [])) if progress else set()
    resources = []
    for filename in ndjson_files:
        filepath = os.path.join(directory, filename)
        for index, resource in enumerate(iter_resources_from_file(filepath), start=1):
            resource_id = resource.get("id")
            resource_key = f"{filename}:{resource_type}/{resource_id}" if resource_id else f"{filename}:{index}"
            if resource_key in completed:
                continue
            resources.append((resource_key, resource))
    return resources


def put_ndjson_resources_concurrently(base_url, directory, resource_type, session, concurrency, progress=None):
    """
    Upload all NDJSON resources of resource_type using a bounded thread pool. Only called
    once ndjson_type_has_self_references() has confirmed no same-type references exist,
    so resources of this type can be safely uploaded in any order/concurrently.
    """
    resources = collect_ndjson_resources_for_type(directory, resource_type, progress=progress)
    if not resources:
        return 0

    print(
        f"⚡ Uploading {len(resources)} {resource_type} resource(s) with up to {concurrency} "
        "concurrent workers (no same-type references detected)...\n"
    )

    uploaded_count = 0
    lock = threading.Lock()
    chunk_stop = False

    def do_upload(resource_key, json_data):
        ok = upload_resource(base_url, json_data, session, source_label=resource_key)
        return resource_key, ok

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        index = 0
        total = len(resources)
        while index < total and not chunk_stop:
            batch = resources[index:index + concurrency]
            index += len(batch)
            futures = [executor.submit(do_upload, *item) for item in batch]
            for future in concurrent.futures.as_completed(futures):
                resource_key, ok = future.result()
                with lock:
                    if ok:
                        uploaded_count += 1
                    if progress:
                        progress.record_result(ok)
                        if ok:
                            progress.data.setdefault("completed_resources", []).append(resource_key)
                        if progress.should_checkpoint():
                            progress.save()
                        if progress.chunk_reached():
                            chunk_stop = True

    if progress and chunk_stop:
        progress.save()
        print(
            f"⏸️  Chunk size reached while concurrently uploading {resource_type}. "
            f"Progress saved to {progress.progress_file}.\n"
        )
        raise ChunkLimitReached()

    return uploaded_count


def upload_ndjson_for_type(base_url, directory, resource_type, session, progress=None, concurrency=1):
    """
    Dispatches NDJSON upload for a single resource type: concurrently when a worker
    count > 1 was requested and a same-type reference scan confirms it's safe, otherwise
    falls back to the existing sequential, position-resumable path.
    """
    if concurrency > 1 and resource_type and not ndjson_type_has_self_references(directory, resource_type):
        return put_ndjson_resources_concurrently(base_url, directory, resource_type, session, concurrency, progress=progress)

    if concurrency > 1 and resource_type:
        print(
            f"⚠️  [INFO] {resource_type} contains same-type references (e.g. partOf/hasMember); "
            "uploading sequentially for correctness.\n"
        )

    return put_ndjson_files(base_url, directory, session, resource_type_filter=resource_type, progress=progress)


def put_files_for_resource_type(base_url, directory, resource_type, session, progress=None, concurrency=1):
    print(f"📦 Processing resource type: {resource_type}\n")
    matching_files = list_fhir_files(directory, resource_type)
    if not matching_files:
        print(f"[INFO] No files found for resource type: {resource_type}\n")
        return 0

    uploaded_count = 0
    uploaded_count += put_json_files(base_url, directory, session, resource_type_filter=resource_type, progress=progress)
    uploaded_count += upload_ndjson_for_type(base_url, directory, resource_type, session, progress=progress, concurrency=concurrency)
    return uploaded_count


def put_files_in_order(base_url, directory, type_order, session, progress=None, concurrency=1):
    print(f"📚 Uploading resources in explicit order: {', '.join(type_order)}\n")
    total_uploaded = 0
    for resource_type in type_order:
        total_uploaded += put_files_for_resource_type(base_url, directory, resource_type, session, progress=progress, concurrency=concurrency)
    return total_uploaded


def put_files(
    base_url,
    directory,
    session,
    resource_type_filter=None,
    type_order_scan_mode=TYPE_ORDER_SCAN_MODE,
    type_order_sample_size=TYPE_ORDER_SAMPLE_SIZE,
    progress=None,
    concurrency=1,
):
    matching_files = list_fhir_files(directory, resource_type_filter)
    if not matching_files:
        print(f"[INFO] No files found for resource type: {resource_type_filter}")
        return 0

    if resource_type_filter is None:
        ndjson_files = list_fhir_files(directory, extensions={".ndjson"})
        if ndjson_files:
            # Use configurable scan behavior for NDJSON type ordering.
            sample_size = 0 if type_order_scan_mode == "full" else type_order_sample_size
            inferred_type_order = infer_type_order(directory, sample_size=sample_size)
            if inferred_type_order:
                total_uploaded = put_files_in_order(base_url, directory, inferred_type_order, session, progress=progress, concurrency=concurrency)
                print("\n🎉 Upload process complete.\n")
                return total_uploaded

    json_uploaded = put_json_files(base_url, directory, session, resource_type_filter=resource_type_filter, progress=progress)
    ndjson_uploaded = upload_ndjson_for_type(base_url, directory, resource_type_filter, session, progress=progress, concurrency=concurrency)

    print("\n🎉 Upload process complete.\n")
    return json_uploaded + ndjson_uploaded

def main():
    parser = argparse.ArgumentParser(description="Upload FHIR JSON files to a server.")
    parser.add_argument('--data', required=True, help='Directory containing FHIR JSON files')
    parser.add_argument('--host', required=True, help='Base URL of the FHIR server including /fhir (e.g. https://example.com/fhir)')
    parser.add_argument('--user', help='Username for basic auth')
    parser.add_argument('--password', help='Password for basic auth')
    parser.add_argument('--type', help='Optional FHIR resource type to filter uploads (e.g. AllergyIntolerance)')
    parser.add_argument('--type-order', help='Comma-separated resource types to upload in order (e.g. Patient,Practitioner,Observation)')
    parser.add_argument(
        '--type-order-scan-mode',
        choices=['sample', 'full'],
        default='full',
        help='How to infer NDJSON type dependencies when --type-order is not provided: sample (faster) or full (more accurate). Default: full',
    )
    parser.add_argument(
        '--type-order-sample-size',
        type=int,
        default=TYPE_ORDER_SAMPLE_SIZE,
        help=f'Per-file sample size for NDJSON dependency inference when --type-order-scan-mode=sample (default: {TYPE_ORDER_SAMPLE_SIZE})',
    )
    parser.add_argument(
        '--chunk-size',
        type=int,
        help='Opt in to chunked uploads: process at most this many resources in this run before pausing. '
             'Progress is saved so re-running the same command resumes automatically. Without this flag, '
             'no progress is tracked and the upload always runs start-to-finish (previous behavior). '
             'When combined with --concurrency > 1, the cutoff is approximate: an in-flight batch of up '
             'to --concurrency resources is allowed to finish rather than being cancelled mid-flight, so '
             'a run may upload up to (concurrency - 1) more resources than --chunk-size before pausing.',
    )
    parser.add_argument(
        '--progress-file',
        help='Path to the progress tracking file when using --chunk-size (default: <data>/.upload-progress.<host>.json)',
    )
    parser.add_argument(
        '--restart',
        action='store_true',
        help='Ignore any existing progress file and re-upload everything from the beginning (requires --chunk-size)',
    )
    parser.add_argument(
        '--concurrency',
        type=int,
        default=1,
        help='Number of concurrent upload workers for NDJSON resources (default: 1, sequential). '
             'Only applied to a resource type\'s NDJSON resources when a same-type-reference scan '
             '(e.g. Organization.partOf, Observation.hasMember) confirms concurrent upload is safe; '
             'otherwise that type falls back to sequential uploads automatically. JSON individual '
             'files and cross-type ordering always remain sequential.',
    )
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()

    if args.type and args.type_order:
        parser.error('--type and --type-order cannot be used together')

    global DEBUG
    DEBUG = args.debug

    if args.type_order_scan_mode == 'sample' and args.type_order_sample_size <= 0:
        parser.error('--type-order-sample-size must be greater than 0')

    if args.chunk_size is not None and args.chunk_size <= 0:
        parser.error('--chunk-size must be greater than 0')

    if not args.chunk_size and (args.progress_file or args.restart):
        parser.error('--progress-file and --restart require --chunk-size')

    if args.concurrency <= 0:
        parser.error('--concurrency must be greater than 0')

    auth = HTTPBasicAuth(args.user, args.password) if args.user and args.password else None

    # A shared Session reuses the underlying TCP/TLS connection (HTTP keep-alive) across all
    # uploads in this run instead of reconnecting per-request, which otherwise dominates
    # per-record latency on high-RTT/VPN network paths.
    session = requests.Session()
    session.auth = auth

    if args.concurrency > 1:
        # Size the connection pool to match the worker count so concurrent requests reuse
        # distinct pooled connections instead of contending for (or repeatedly opening) one.
        adapter = requests.adapters.HTTPAdapter(pool_connections=args.concurrency, pool_maxsize=args.concurrency)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

    progress = None
    if args.chunk_size:
        progress_file = args.progress_file or default_progress_file(args.data, args.host)
        progress = ProgressTracker(progress_file, chunk_size=args.chunk_size)

        if args.restart:
            progress.reset()
            print(f"🔄 Restarting upload: cleared progress file {progress_file}\n")
        elif progress.data.get("completed"):
            print(
                f"✅ Progress file {progress_file} shows this upload already completed "
                f"(total_uploaded={progress.data['total_uploaded']}). Use --restart to re-upload.\n"
            )
            return
        elif progress.data["files"]:
            print(
                f"↩️  Resuming from progress file: {progress_file} "
                f"(previously processed {progress.data['total_processed']}, uploaded {progress.data['total_uploaded']})\n"
            )

    start_time = datetime.now()
    start_perf = time.perf_counter()
    print(f"🕒 Upload started: {start_time.isoformat(timespec='seconds')}\n")

    incomplete = False
    total_uploaded = 0
    try:
        if args.type_order:
            type_order = [resource_type.strip() for resource_type in args.type_order.split(',') if resource_type.strip()]
            if not type_order:
                parser.error('--type-order must contain at least one resource type')
            total_uploaded = put_files_in_order(args.host.rstrip("/"), args.data, type_order, session, progress=progress, concurrency=args.concurrency)
        else:
            total_uploaded = put_files(
                args.host.rstrip("/"),
                args.data,
                session,
                args.type,
                args.type_order_scan_mode,
                args.type_order_sample_size,
                progress=progress,
                concurrency=args.concurrency,
            )
    except ChunkLimitReached:
        incomplete = True
        total_uploaded = progress.uploaded_this_run if progress else total_uploaded
    except KeyboardInterrupt:
        print("\n⚠️ [WARNING] Interrupted by user.")
        if progress:
            progress.save()
            print(f"💾 Progress saved to {progress.progress_file}. Re-run the same command to resume.\n")
        sys.exit(130)
    finally:
        session.close()

    if progress and not incomplete:
        progress.data["completed"] = True
        progress.save()
        total_uploaded = progress.uploaded_this_run

    end_time = datetime.now()
    total_elapsed_seconds = time.perf_counter() - start_perf
    upload_elapsed_seconds = UPLOAD_TIMER.elapsed_seconds()
    total_uploaded = total_uploaded or 0
    # Rate is based on time actually spent uploading (first request sent to last response
    # received), excluding upfront setup work such as building dependency maps, topologically
    # sorting files, or inferring NDJSON type order - that setup can take a while on large
    # datasets and isn't part of real upload throughput.
    rate = (total_uploaded / upload_elapsed_seconds) if upload_elapsed_seconds > 0 else 0

    print(f"🕒 Upload finished: {end_time.isoformat(timespec='seconds')}")
    setup_elapsed_seconds = max(total_elapsed_seconds - upload_elapsed_seconds, 0)
    print(
        f"🧮 Setup (dependency mapping/sorting/type-order inference): {setup_elapsed_seconds:.2f}s\n"
        f"📊 Uploaded {total_uploaded} record(s) in {upload_elapsed_seconds:.2f}s of actual upload time "
        f"({rate:.2f} records/sec); total run time {total_elapsed_seconds:.2f}s\n"
    )
    if progress and progress.data['total_uploaded'] != total_uploaded:
        print(
            f"ℹ️  Lifetime total for this progress file: {progress.data['total_uploaded']} record(s) "
            "(includes earlier runs resumed from this progress file).\n"
        )

    if incomplete:
        print(
            f"⏸️  Chunk size limit ({args.chunk_size}) reached before all data was uploaded. "
            "Re-run the same command to continue from where it left off.\n"
        )
        sys.exit(EXIT_CODE_INCOMPLETE)

if __name__ == "__main__":
    main()