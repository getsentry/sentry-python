# Analysis: `extract_span_data` Function Cleanup

## Current Function Overview

The `extract_span_data` function in `sentry_sdk/opentelemetry/utils.py` (lines 113-147) extracts span operation, description, status, HTTP status, and origin from OpenTelemetry ReadableSpan objects. It returns a tuple: `(op, description, status, http_status, origin)`.

## Current Issues

### 1. **Complex Control Flow with Mixed Responsibilities**
- The function handles multiple span types (HTTP, DB, RPC, messaging, FAAS) with inconsistent patterns
- Some types delegate to helper functions while others are handled inline
- Early returns make the logic flow hard to follow

### 2. **Repeated Attribute Extraction**
- Basic attributes (`op`, `description`, `origin`) are extracted multiple times
- Each span type re-extracts these common attributes
- No clear separation between common and type-specific logic

### 3. **Inconsistent Delegation Pattern**
- HTTP spans → `span_data_for_http_method()` 
- DB spans → `span_data_for_db_query()`
- RPC/messaging/FAAS spans → handled inline with duplicate logic

### 4. **Poor Readability and Maintainability**
- Long if-elif chain makes priority order unclear
- Magic string comparisons scattered throughout
- Helper function calls buried in conditional logic

### 5. **Inefficient Attribute Access**
- Multiple calls to `get_typed_attribute()` for the same attributes
- Redundant null checks on `span.attributes`

## Suggested Refactoring Approach

### Option 1: Strategy Pattern with Span Type Handlers

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

class SpanType(Enum):
    HTTP = "http"
    DATABASE = "database" 
    RPC = "rpc"
    MESSAGING = "messaging"
    FAAS = "faas"
    GENERIC = "generic"

@dataclass
class SpanContext:
    """Common span data extracted once"""
    span: ReadableSpan
    attributes: dict
    base_op: str
    base_description: str
    status: Optional[str]
    http_status: Optional[int]
    origin: Optional[str]

class SpanHandler(ABC):
    @abstractmethod
    def can_handle(self, context: SpanContext) -> bool:
        pass
    
    @abstractmethod
    def extract_data(self, context: SpanContext) -> OtelExtractedSpanData:
        pass

class HttpSpanHandler(SpanHandler):
    def can_handle(self, context: SpanContext) -> bool:
        return SpanAttributes.HTTP_METHOD in context.attributes
    
    def extract_data(self, context: SpanContext) -> OtelExtractedSpanData:
        # Move span_data_for_http_method logic here
        return span_data_for_http_method(context.span)

def extract_span_data(span: ReadableSpan) -> OtelExtractedSpanData:
    context = _build_span_context(span)
    
    handlers = [
        HttpSpanHandler(),
        DatabaseSpanHandler(), 
        RpcSpanHandler(),
        MessagingSpanHandler(),
        FaasSpanHandler(),
        GenericSpanHandler()  # fallback
    ]
    
    for handler in handlers:
        if handler.can_handle(context):
            return handler.extract_data(context)
    
    return (context.base_op, context.base_description, 
            context.status, context.http_status, context.origin)
```

### Option 2: Simplified Functional Approach

```python
def extract_span_data(span: ReadableSpan) -> OtelExtractedSpanData:
    # Extract common data once
    common_data = _extract_common_span_data(span)
    
    # Determine span type and delegate
    span_type = _determine_span_type(span.attributes or {})
    
    return _SPAN_EXTRACTORS[span_type](span, common_data)

def _extract_common_span_data(span: ReadableSpan) -> dict:
    """Extract attributes common to all span types"""
    attributes = span.attributes or {}
    status, http_status = extract_span_status(span)
    
    return {
        'attributes': attributes,
        'base_op': span.name,
        'base_description': span.name,
        'status': status,
        'http_status': http_status,
        'custom_op': get_typed_attribute(attributes, SentrySpanAttribute.OP, str),
        'custom_description': get_typed_attribute(attributes, SentrySpanAttribute.DESCRIPTION, str),
        'origin': get_typed_attribute(attributes, SentrySpanAttribute.ORIGIN, str)
    }

def _determine_span_type(attributes: dict) -> str:
    """Determine span type based on attributes"""
    if SpanAttributes.HTTP_METHOD in attributes:
        return 'http'
    elif SpanAttributes.DB_SYSTEM in attributes:
        return 'database'
    elif SpanAttributes.RPC_SERVICE in attributes:
        return 'rpc'
    elif SpanAttributes.MESSAGING_SYSTEM in attributes:
        return 'messaging'
    elif SpanAttributes.FAAS_TRIGGER in attributes:
        return 'faas'
    else:
        return 'generic'

_SPAN_EXTRACTORS = {
    'http': _extract_http_span_data,
    'database': _extract_db_span_data,
    'rpc': _extract_rpc_span_data,
    'messaging': _extract_messaging_span_data,
    'faas': _extract_faas_span_data,
    'generic': _extract_generic_span_data
}
```

### Option 3: Minimal Refactoring (Recommended for Immediate Improvement)

```python
def extract_span_data(span: ReadableSpan) -> OtelExtractedSpanData:
    """Extract span operation, description, status, and origin data."""
    # Early return for spans without attributes
    if span.attributes is None:
        status, http_status = extract_span_status(span)
        return (span.name, span.name, status, http_status, None)
    
    # Extract common attributes once
    common = _extract_common_attributes(span)
    
    # Delegate to type-specific extractors
    if SpanAttributes.HTTP_METHOD in span.attributes:
        return span_data_for_http_method(span)
    elif SpanAttributes.DB_SYSTEM in span.attributes:
        return span_data_for_db_query(span)
    elif SpanAttributes.RPC_SERVICE in span.attributes:
        return _build_span_data(common['op'] or "rpc", common)
    elif SpanAttributes.MESSAGING_SYSTEM in span.attributes:
        return _build_span_data(common['op'] or "message", common)
    elif SpanAttributes.FAAS_TRIGGER in span.attributes:
        faas_trigger = str(span.attributes[SpanAttributes.FAAS_TRIGGER])
        return _build_span_data(faas_trigger, common)
    else:
        return _build_span_data(common['op'], common)

def _extract_common_attributes(span: ReadableSpan) -> dict:
    """Extract attributes common to all span types."""
    attributes = span.attributes or {}
    status, http_status = extract_span_status(span)
    
    return {
        'op': get_typed_attribute(attributes, SentrySpanAttribute.OP, str) or span.name,
        'description': (
            get_typed_attribute(attributes, SentrySpanAttribute.DESCRIPTION, str) 
            or span.name
        ),
        'status': status,
        'http_status': http_status,
        'origin': get_typed_attribute(attributes, SentrySpanAttribute.ORIGIN, str)
    }

def _build_span_data(op: str, common: dict) -> OtelExtractedSpanData:
    """Build the final span data tuple."""
    return (op, common['description'], common['status'], 
            common['http_status'], common['origin'])
```

## Recommended Implementation

**Option 3 (Minimal Refactoring)** is recommended because:

1. **Low Risk**: Maintains existing behavior with minimal changes
2. **Immediate Benefits**: Eliminates duplicate code and improves readability
3. **Incremental**: Can be applied now, with further refactoring later
4. **Testable**: Easy to verify that behavior remains unchanged

## Additional Improvements

1. **Add Constants**: Define span type detection constants
2. **Better Error Handling**: Handle edge cases in attribute extraction
3. **Documentation**: Add comprehensive docstrings explaining the extraction logic
4. **Unit Tests**: Ensure comprehensive test coverage for all span types

## Migration Strategy

1. Extract common attribute logic (low risk)
2. Consolidate type-specific handlers (medium risk) 
3. Consider strategy pattern for new span types (future enhancement)