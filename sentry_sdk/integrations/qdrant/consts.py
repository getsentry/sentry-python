from sentry_sdk.integrations.qdrant.path_matching import PathTrie

SPAN_ORIGIN = "auto.db.qdrant"

# created from https://github.com/qdrant/qdrant/blob/master/docs/redoc/v1.11.x/openapi.json
# only used for qdrants REST API. gRPC is using other identifiers
_PATH_TO_OPERATION_ID = {
    "/collections/{collection_name}/shards": {"put": "create_shard_key"},
    "/collections/{collection_name}/shards/delete": {"post": "delete_shard_key"},
    "/": {"get": "root"},
    "/telemetry": {"get": "telemetry"},
    "/metrics": {"get": "metrics"},
    "/locks": {"post": "post_locks", "get": "get_locks"},
    "/healthz": {"get": "healthz"},
    "/livez": {"get": "livez"},
    "/readyz": {"get": "readyz"},
    "/issues": {"get": "get_issues", "delete": "clear_issues"},
    "/cluster": {"get": "cluster_status"},
    "/cluster/recover": {"post": "recover_current_peer"},
    "/cluster/peer/{peer_id}": {"delete": "remove_peer"},
    "/collections": {"get": "get_collections"},
    "/collections/{collection_name}": {
        "get": "get_collection",
        "put": "create_collection",
        "patch": "update_collection",
        "delete": "delete_collection",
    },
    "/collections/aliases": {"post": "update_aliases"},
    "/collections/{collection_name}/index": {"put": "create_field_index"},
    "/collections/{collection_name}/exists": {"get": "collection_exists"},
    "/collections/{collection_name}/index/{field_name}": {
        "delete": "delete_field_index"
    },
    "/collections/{collection_name}/cluster": {
        "get": "collection_cluster_info",
        "post": "update_collection_cluster",
    },
    "/collections/{collection_name}/aliases": {"get": "get_collection_aliases"},
    "/aliases": {"get": "get_collections_aliases"},
    "/collections/{collection_name}/snapshots/upload": {
        "post": "recover_from_uploaded_snapshot"
    },
    "/collections/{collection_name}/snapshots/recover": {
        "put": "recover_from_snapshot"
    },
    "/collections/{collection_name}/snapshots": {
        "get": "list_snapshots",
        "post": "create_snapshot",
    },
    "/collections/{collection_name}/snapshots/{snapshot_name}": {
        "delete": "delete_snapshot",
        "get": "get_snapshot",
    },
    "/snapshots": {"get": "list_full_snapshots", "post": "create_full_snapshot"},
    "/snapshots/{snapshot_name}": {
        "delete": "delete_full_snapshot",
        "get": "get_full_snapshot",
    },
    "/collections/{collection_name}/shards/{shard_id}/snapshots/upload": {
        "post": "recover_shard_from_uploaded_snapshot"
    },
    "/collections/{collection_name}/shards/{shard_id}/snapshots/recover": {
        "put": "recover_shard_from_snapshot"
    },
    "/collections/{collection_name}/shards/{shard_id}/snapshots": {
        "get": "list_shard_snapshots",
        "post": "create_shard_snapshot",
    },
    "/collections/{collection_name}/shards/{shard_id}/snapshots/{snapshot_name}": {
        "delete": "delete_shard_snapshot",
        "get": "get_shard_snapshot",
    },
    "/collections/{collection_name}/points/{id}": {"get": "get_point"},
    "/collections/{collection_name}/points": {
        "post": "get_points",
        "put": "upsert_points",
    },
    "/collections/{collection_name}/points/delete": {"post": "delete_points"},
    "/collections/{collection_name}/points/vectors": {"put": "update_vectors"},
    "/collections/{collection_name}/points/vectors/delete": {"post": "delete_vectors"},
    "/collections/{collection_name}/points/payload": {
        "post": "set_payload",
        "put": "overwrite_payload",
    },
    "/collections/{collection_name}/points/payload/delete": {"post": "delete_payload"},
    "/collections/{collection_name}/points/payload/clear": {"post": "clear_payload"},
    "/collections/{collection_name}/points/batch": {"post": "batch_update"},
    "/collections/{collection_name}/points/scroll": {"post": "scroll_points"},
    "/collections/{collection_name}/points/search": {"post": "search_points"},
    "/collections/{collection_name}/points/search/batch": {
        "post": "search_batch_points"
    },
    "/collections/{collection_name}/points/search/groups": {
        "post": "search_point_groups"
    },
    "/collections/{collection_name}/points/recommend": {"post": "recommend_points"},
    "/collections/{collection_name}/points/recommend/batch": {
        "post": "recommend_batch_points"
    },
    "/collections/{collection_name}/points/recommend/groups": {
        "post": "recommend_point_groups"
    },
    "/collections/{collection_name}/points/discover": {"post": "discover_points"},
    "/collections/{collection_name}/points/discover/batch": {
        "post": "discover_batch_points"
    },
    "/collections/{collection_name}/points/count": {"post": "count_points"},
    "/collections/{collection_name}/points/query": {"post": "query_points"},
    "/collections/{collection_name}/points/query/batch": {"post": "query_batch_points"},
    "/collections/{collection_name}/points/query/groups": {
        "post": "query_points_groups"
    },
}

_DISALLOWED_PROTO_FIELDS = {"data", "keyword"}

_DISALLOWED_REST_FIELDS = {"nearest", "value"}

_IDENTIFIER = "qdrant"

_qdrant_trie = PathTrie(_PATH_TO_OPERATION_ID)
