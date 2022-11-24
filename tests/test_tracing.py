# @classmethod
# def extract_sentry_trace(cls, sentry_trace):
#     # type: (str) -> Tuple[str, str, bool]
#     trace_id, parent_span_id, parent_sampled = sentry_trace.split("-")
#     parent_sampled = True if parent_sampled == "1" else False
#     return (trace_id, parent_span_id, parent_sampled)


# Transaction.extract_sentry_trace(sentry_trace[0])
