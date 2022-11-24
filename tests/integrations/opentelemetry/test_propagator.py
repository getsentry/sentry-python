# test extract

#   no context, no SENTRY_TRACE_HEADER_NAME in getter
#       should return context as is
#   some context, no SENTRY_TRACE_HEADER_NAME in getter
#       should return context as is

#   no context but sentry trace data and baggage in getter
#       return context should have baggage in it and also a noopspan with trace data (span and trace id)

#   no context but sentry trace data and NO baggage in getter
#       return context should have baggage in it and also a noopspan with trace data (span and trace id)


#   some context but sentry trace data and baggage in getter
#       return context should have given context and baggage in it and also a noopspan with trace data (span and trace id)

#   some context but sentry trace data and NO baggage in getter
#       return context should have given context and empty baggage in it and also a noopspan with trace data (span and trace id)


# test inject

# give empty otel_span_map
#   should return none

# give empty otel_span_map with sentry span and also a context that has span with span id. the span id in otel_span_map and context should NOT match
#   should return none

# give otel_span_map with sentry span with matching span id, without baggage
#   check that setter.set is called with sentry span to_traceparent() but no call to setter.set baggage header

# give otel_span_map with sentry span with baggage
#   call to setter.set baggage header
