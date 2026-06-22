from typing import Any, Dict, Optional, List


class TrieNode:
    def __init__(self, is_placeholder=False):
        """
        Initializes a TrieNode.

        :param is_placeholder: Indicates if this node represents a placeholder (wildcard).
        """
        self.children = {}  # type: Dict[str, 'TrieNode']
        self.operation_ids = {}  # type: Dict[str, str]
        self.is_placeholder = is_placeholder  # type: bool

    @classmethod
    def from_dict(cls, data, parent_path=""):
        # type: (Dict[str, Any], str) -> 'TrieNode'
        """
        Recursively constructs a TrieNode from a nested dictionary.

        :param data: Nested dictionary mapping path segments to either nested dictionaries
                     or dictionaries of HTTP methods to operation IDs.
        :param parent_path: The accumulated path from the root to the current node.
        :return: Root TrieNode of the constructed trie.
        """
        node = cls()
        for path, methods in data.items():
            segments = PathTrie.split_path(path)
            current = node
            for segment in segments:
                is_placeholder = segment.startswith("{") and segment.endswith("}")
                key = "*" if is_placeholder else segment

                if key not in current.children:
                    current.children[key] = TrieNode(is_placeholder=is_placeholder)
                current = current.children[key]

            if isinstance(methods, dict):
                for method, operation_id in methods.items():
                    current.operation_ids[method.lower()] = operation_id

        return node

    def to_dict(self, current_path=""):
        # type: (str) -> Dict[str, Any]
        """
        Serializes the TrieNode and its children back to a nested dictionary.

        :param current_path: The accumulated path from the root to the current node.
        :return: Nested dictionary representing the trie.
        """
        result = {}  # type: Dict[str, Any]
        if self.operation_ids:
            path_key = current_path or "/"
            result[path_key] = self.operation_ids.copy()

        for segment, child in self.children.items():
            # replace wildcard '*' back to placeholder format if necessary.
            # allows for TrieNode.from_dict(TrieNode.to_dict()) to be idempotent.
            display_segment = "{placeholder}" if child.is_placeholder else segment
            new_path = (
                f"{current_path}/{display_segment}"
                if current_path
                else f"/{display_segment}"
            )
            child_dict = child.to_dict(new_path)
            result.update(child_dict)

        return result


class PathTrie:
    WILDCARD = "*"  # type: str

    def __init__(self, data=None):
        # type: (Optional[Dict[str, Any]]) -> None
        """
        Initializes the PathTrie with optional initial data.

        :param data: Optional nested dictionary to initialize the trie.
        """
        self.root = TrieNode.from_dict(data or {})  # type: TrieNode

    def insert(self, path, method, operation_id):
        # type: (str, str, str) -> None
        """
        Inserts a path into the trie with its corresponding HTTP method and operation ID.

        :param path: The API path (e.g., '/users/{user_id}/posts').
        :param method: HTTP method (e.g., 'GET', 'POST').
        :param operation_id: The operation identifier associated with the path and method.
        """
        current = self.root
        segments = self.split_path(path)

        for segment in segments:
            is_placeholder = self._is_placeholder(segment)
            key = self.WILDCARD if is_placeholder else segment

            if key not in current.children:
                current.children[key] = TrieNode(is_placeholder=is_placeholder)
            current = current.children[key]

        current.operation_ids[method.lower()] = operation_id

    def match(self, path, method):
        # type: (str, str) -> Optional[str]
        """
        Matches a given path and HTTP method to its corresponding operation ID.

        :param path: The API path to match.
        :param method: HTTP method to match.
        :return: The operation ID if a match is found; otherwise, None.
        """
        current = self.root
        segments = self.split_path(path)

        for segment in segments:
            if segment in current.children:
                current = current.children[segment]
            elif self.WILDCARD in current.children:
                current = current.children[self.WILDCARD]
            else:
                return None

        return current.operation_ids.get(method.lower())

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return self.root.to_dict()

    @staticmethod
    def split_path(path):
        # type: (str) -> List[str]
        return [segment for segment in path.strip("/").split("/") if segment]

    @staticmethod
    def _is_placeholder(segment):
        # type: (str) -> bool
        return segment.startswith("{") and segment.endswith("}")

    def __repr__(self):
        # type: () -> str
        return f"PathTrie({self.to_dict()})"
