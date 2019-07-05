import json
import sys

print("digraph mytrace {")
print("rankdir=LR")

all_spans = []

for line in sys.stdin:
    event = json.loads(line)
    if event.get("type") != "transaction":
        continue

    trace_ctx = event["contexts"]["trace"]
    trace_span = dict(trace_ctx)  # fake a span entry from transaction event
    trace_span["description"] = event["transaction"]
    trace_span["start_timestamp"] = event["start_timestamp"]
    trace_span["timestamp"] = event["timestamp"]

    if "parent_span_id" not in trace_ctx:
        print(
            '{} [label="trace:{} ({})"];'.format(
                int(trace_ctx["trace_id"], 16),
                event["transaction"],
                trace_ctx["trace_id"],
            )
        )

    for span in event["spans"] + [trace_span]:
        print(
            '{} [label="span:{} ({})"];'.format(
                int(span["span_id"], 16), span["description"], span["span_id"]
            )
        )
        if "parent_span_id" in span:
            print(
                "{} -> {};".format(
                    int(span["parent_span_id"], 16), int(span["span_id"], 16)
                )
            )

        print(
            "{} -> {} [style=dotted];".format(
                int(span["trace_id"], 16), int(span["span_id"], 16)
            )
        )

        all_spans.append(span)


for s1 in all_spans:
    for s2 in all_spans:
        if s1["start_timestamp"] > s2["timestamp"]:
            print(
                '{} -> {} [color="#efefef"];'.format(
                    int(s1["span_id"], 16), int(s2["span_id"], 16)
                )
            )


print("}")
