# Protocol

The features protocol is remote-configuration oriented. Meaning we focus on returning a value (`bool`, `float`, `int`, `object`, `str`) above all else. `key` and `value` properties are always defined at the root of a feature object.

Variants may be optionally supplied which allow the feature provider to change the `value` returned to the user.

## Variants

Variants are optionally provided. Variants may return a value or they may not. If they do not return a value continue to the next variant. If no variant returns a value, return the root `value`. Variants are ordered. If a variant is matched we break out of the variant processing loop and return the variant's value.

## Distribution

The `distribution` key enables random distribution among cohorts for A/B testing purposes. The sum of all the percentages in the list must equal to `100`. If it does not an error will be returned by the feature provider. If A/B testing is not desired a single distribution left at 100% is defined.

Deterministic distribution is managed by the `rollout` property.

## Rollout

Rollouts can be `random` or `seed`ed. `seed` based rollouts have a targeting key defined under the `seed` attribute. Rollouts do not need to add up to 100%. Given a local context none, some, or all rollouts could be matched. The convention is that the first variant matched is returned.

| Type   | Description                                                |
| ------ | ---------------------------------------------------------- |
| seed   | A hash of the `seed` property is used to determine cohort. |
| random | A random number generator is used to determine cohort.     |

## Rules

Rules describe a set of conditions that should be applied against a local context. `value` is a literal and `property` is the attribute name of the context. `operator` describes the operation being performed. `hint` is optionally specified to describe any further parsing required to resolve the `value` field.

All rules belonging to a variant are `AND`ed together. `OR` is not supported.

| Operator | Description                               | Types                |
| -------- | ----------------------------------------- | -------------------- |
| ==       | Equals.                                   | Any                  |
| !=       | Not equals.                               | Any                  |
| >=       | Greater than or equal to.                 | datetime, float, int |
| >        | Greater than.                             | datetime, float, int |
| <=       | Less than or equal to.                    | datetime, float, int |
| <        | Less than.                                | datetime, float, int |
| in       | Contains.                                 | hash map, list, str  |
| not in   | Does not contain.                         | hash map, list, str  |
| glob     | Match a pattern. \*str, str\*, or \*str\* | str                  |

## Example

```json
{
  "version": 1,
  "features": [
    {
      "key": "abc",
      "value": "1",
      "variants": [
        {
          "id": "e7008a3fc1dd4a97bfdcf918d7f44530",
          "distribution": [
            {
              "id": "e88a6b0afe4d427a95c90e129c584698",
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
