class MockUnleashClient:

    def __init__(self, *a, **kw):
        self.features = {
            "hello": True,
            "world": False,
        }

        self.feature_to_variant = {
            "string_feature": {
                "name": "variant1",
                "enabled": True,
                "payload": {"type": "string", "value": "val1"},
            },
            "json_feature": {
                "name": "variant1",
                "enabled": True,
                "payload": {"type": "json", "value": '{"key1": 0.53}'},
            },
            "number_feature": {
                "name": "variant1",
                "enabled": True,
                "payload": {"type": "number", "value": "134.5"},
            },
            "csv_feature": {
                "name": "variant1",
                "enabled": True,
                "payload": {"type": "csv", "value": "abc 123\ncsbq 94"},
            },
            "toggle_feature": {"name": "variant1", "enabled": True},
        }

        self.disabled_variant = {"name": "disabled", "enabled": False}

    def is_enabled(self, feature, *a, **kw):
        return self.features.get(feature, False)

    def get_variant(self, feature, *a, **kw):
        return self.feature_to_variant.get(feature, self.disabled_variant)
