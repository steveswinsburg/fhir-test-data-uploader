import sys
import argparse
import requests
from requests.auth import HTTPBasicAuth

DEBUG = False
DEFAULT_PAGE_SIZE = 100


def log(message):
    if DEBUG:
        print(message)


def iter_matching_ids(base_url, resource_type, prefix, session, page_size):
    """Yield resource IDs of the given type whose ID starts with prefix, from a single
    search request (no paging/"next"-link following). FHIR has no server-side "ID starts
    with" filter, so a match can be anywhere in the result set - increase --page-size if
    matches aren't being found, so the single request's _count covers the full result set.
    Only the id is requested (_elements=id) to keep the response small."""
    url = f"{base_url}/{resource_type}"
    params = {"_elements": "id", "_count": page_size}

    response = session.get(url, params=params, headers={"Accept": "application/fhir+json"})
    if response.status_code == 404:
        log(f"ℹ️  [INFO] {resource_type} search returned 404; skipping")
        return
    response.raise_for_status()
    bundle = response.json()

    for entry in bundle.get("entry", []):
        resource_id = entry.get("resource", {}).get("id", "")
        if resource_id.startswith(prefix):
            yield resource_id


def delete_resource(base_url, resource_type, resource_id, session, ignore_references=False):
    url = f"{base_url}/{resource_type}/{resource_id}"
    headers = {"Accept": "application/fhir+json"}
    if ignore_references:
        # Tells a HAPI FHIR server to cascade-delete anything still referencing this
        # resource instead of rejecting the delete with a 409 Conflict.
        headers["X-Cascade"] = "delete"
    try:
        response = session.delete(url, headers=headers)
    except Exception as e:
        print(f"❌ [ERROR] Failed to DELETE {resource_type}/{resource_id}: {e}")
        return False

    if response.ok or response.status_code == 404:
        return True

    print(f"❌ [ERROR] DELETE {resource_type}/{resource_id} failed - Status: {response.status_code} - {response.text[:300]}")
    return False


def main():
    parser = argparse.ArgumentParser(
        description='Delete FHIR resources whose ID starts with a given prefix, across all resource types.'
    )
    parser.add_argument('--host', required=True, help='Base URL of the FHIR server including /fhir (e.g. https://example.com/fhir)')
    parser.add_argument('--prefix', default='example-healthconnect-', help='Delete resources whose ID starts with this prefix (default: example-healthconnect-)')
    parser.add_argument('--user', help='Username for basic auth')
    parser.add_argument('--password', help='Password for basic auth')
    parser.add_argument('--types', required=True, help='Comma-separated list of resource types to search (e.g. Patient,Observation)')
    parser.add_argument('--page-size', type=int, default=DEFAULT_PAGE_SIZE, help=f'Number of resources to search per request when looking for a prefix match (default: {DEFAULT_PAGE_SIZE}); deletes still process one resource at a time')
    parser.add_argument('--dry-run', action='store_true', help='List matching resources without deleting them')
    parser.add_argument('--yes', action='store_true', help='Skip the confirmation prompt before deleting')
    parser.add_argument(
        '--ignore-references',
        action='store_true',
        help="Delete even if other resources still reference the one being deleted, by sending "
             "'X-Cascade: delete' so a HAPI FHIR server cascade-deletes the referencing resources "
             "instead of rejecting the delete with a 409 Conflict",
    )
    parser.add_argument('--debug', action='store_true', help='Enable verbose logging')
    args = parser.parse_args()

    if args.page_size <= 0:
        parser.error('--page-size must be greater than 0')

    global DEBUG
    DEBUG = args.debug

    resource_types = [t.strip() for t in args.types.split(',') if t.strip()]
    if not resource_types:
        parser.error('--types must contain at least one resource type')

    if not args.dry_run and not args.yes:
        answer = input(
            f"⚠️  Delete resources with IDs starting with '{args.prefix}' from {args.host} "
            f"across types {', '.join(resource_types)}? Type 'yes' to confirm: "
        )
        if answer.strip().lower() != 'yes':
            print("🚫 Aborted; nothing was deleted.")
            return

    auth = HTTPBasicAuth(args.user, args.password) if args.user and args.password else None
    session = requests.Session()
    session.auth = auth

    base_url = args.host.rstrip('/')

    print(f"🔍 Searching {len(resource_types)} resource type(s) for IDs starting with '{args.prefix}'...\n")

    found_count = 0
    deleted_count = 0
    failed_count = 0

    for resource_type in resource_types:
        log(f"ℹ️  [INFO] Searching {resource_type}...")

        if args.dry_run:
            try:
                for resource_id in iter_matching_ids(base_url, resource_type, args.prefix, session, args.page_size):
                    found_count += 1
                    print(f"  📌 {resource_type}/{resource_id}")
            except requests.exceptions.RequestException as e:
                print(f"⚠️  [WARNING] Failed to search {resource_type}: {e}")
            continue

        try:
            matching_ids = list(iter_matching_ids(base_url, resource_type, args.prefix, session, args.page_size))
        except requests.exceptions.RequestException as e:
            print(f"⚠️  [WARNING] Failed to search {resource_type}: {e}")
            continue

        found_count += len(matching_ids)
        for resource_id in matching_ids:
            print(f"  📌 {resource_type}/{resource_id}")

        # Delete one at a time, but retry anything that fails (e.g. a 409 Conflict because
        # another resource of the same type still references it, such as Organization/Location
        # partOf hierarchies). Each full retry round may unblock resources whose blockers were
        # deleted in an earlier round, so keep going until a round makes no progress at all.
        pending = matching_ids
        round_num = 1
        while pending:
            if round_num > 1:
                print(f"🔁 Retrying {len(pending)} resource(s) in {resource_type} (round {round_num})...")

            still_pending = []
            deleted_this_round = 0
            for resource_id in pending:
                if delete_resource(base_url, resource_type, resource_id, session, ignore_references=args.ignore_references):
                    deleted_count += 1
                    deleted_this_round += 1
                    print(f"🗑️  Deleted {resource_type}/{resource_id}")
                else:
                    still_pending.append(resource_id)

            if deleted_this_round == 0:
                failed_count += len(still_pending)
                for resource_id in still_pending:
                    print(
                        f"⚠️  [WARNING] Giving up on {resource_type}/{resource_id}; still failing after "
                        "retries (likely still referenced by another resource)."
                    )
                break

            pending = still_pending
            round_num += 1

    print(f"\n📊 Found {found_count} matching resource(s).")

    if args.dry_run:
        print("🧪 Dry run: no resources were deleted.")
        return

    print(f"\n🎉 Done. Deleted {deleted_count} resource(s), {failed_count} failure(s).")
    if failed_count:
        sys.exit(1)


if __name__ == '__main__':
    main()
