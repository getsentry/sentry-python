```json
{
  "version": 1,
  "features": [
    {
      "key": "abc",
      "value": "1",
      "variants": [
        {
          "distribution": [
            {
              "percentage": 100,
              "value": "2"
            }
          ],
          "rollout": {
            "type": "seed",
            "data": {
              "percentage": 100,
              "seed": "user_id"
            }
          },
          "rules": [
            {
              "operator": "==",
              "property": "region",
              "value": "Europe"
            },
            {
              "hint": "date",
              "operator": ">=",
              "property": "current_datetime",
              "value": "2024-05-01"
            },
            {
              "operator": "<",
              "property": "score",
              "value": 42
            }
          ]
        }
      ]
    }
  ]
}
```
