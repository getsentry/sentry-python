# Files Still Requiring Type Annotation Migration

This document lists all files in the Sentry Python SDK that still contain comment-based type annotations (`# type:`) and need to be migrated to inline type annotations.

## Core SDK Files (7 files) 🔴

### High Priority Core Files

1. **`sentry_sdk/serializer.py`** - CRITICAL 🚨
   - 40+ comment-based type annotations
   - Core data serialization functionality
   - Complex nested function signatures

2. **`sentry_sdk/sessions.py`** - CRITICAL 🚨
   - 20+ comment-based type annotations
   - Session management and aggregation
   - Threading and background processing

3. **`sentry_sdk/transport.py`** - CRITICAL 🚨
   - 50+ comment-based type annotations
   - HTTP transport and rate limiting
   - Complex network handling logic

4. **`sentry_sdk/spotlight.py`** - Medium Priority
   - 10+ comment-based type annotations
   - Development debugging integration
   - Django middleware patterns

5. **`sentry_sdk/_init_implementation.py`** - Low Priority
   - 2 comment-based type annotations
   - Internal initialization logic

### AI Module Files (2 files)

6. **`sentry_sdk/ai/utils.py`** - Medium Priority
   - 2 comment-based type annotations
   - AI monitoring utilities

7. **`sentry_sdk/ai/monitoring.py`** - Medium Priority
   - 7 comment-based type annotations
   - AI span and token counting functionality

## Integration Files (15+ files) 🟡

### Major Web Frameworks

8. **`sentry_sdk/integrations/litestar.py`** - High Priority
   - 20+ comment-based type annotations
   - Modern ASGI framework integration
   - Complex middleware patterns

9. **`sentry_sdk/integrations/logging.py`** - High Priority
   - 25+ comment-based type annotations
   - Python logging integration
   - Event processing and filtering

10. **`sentry_sdk/integrations/loguru.py`** - Medium Priority
    - 10+ comment-based type annotations
    - Loguru logging integration
    - Custom formatting and levels

### Task Queue and Job Processing

11. **`sentry_sdk/integrations/arq.py`** - Medium Priority
    - 15+ comment-based type annotations
    - Async Redis queue integration
    - Cron job monitoring

### Feature Flag Providers

12. **`sentry_sdk/integrations/launchdarkly.py`** - Low Priority
    - 5 comment-based type annotations
    - Feature flag evaluation tracking

### AI/ML Integrations

13. **`sentry_sdk/integrations/huggingface_hub.py`** - Low Priority
    - 5 comment-based type annotations
    - HuggingFace model hub integration

### System and Utility Integrations

14. **`sentry_sdk/integrations/modules.py`** - Low Priority
    - 2 comment-based type annotations
    - Module tracking functionality

15. **`sentry_sdk/profiler/utils.py`** - Medium Priority
    - 10+ comment-based type annotations
    - Performance profiling utilities
    - Frame processing and stack extraction

## Script Files (3 files) 🟠

These are development/build scripts and lower priority:

16. **`scripts/init_serverless_sdk.py`**
    - 1 comment-based type annotation

17. **`scripts/build_aws_lambda_layer.py`**
    - 10+ comment-based type annotations
    - AWS Lambda deployment script

18. **`scripts/migrate_type_annotations.py`**
    - Contains migration script itself (references to migration patterns)

## Test Files (50+ files) 🔵

Test files are lowest priority but should eventually be migrated for consistency:
- Multiple files in `tests/` directory
- Integration test files
- Unit test files

## Migration Priority Levels

### 🚨 **CRITICAL (Must Complete First)**
- `serializer.py` - Core data processing
- `sessions.py` - Session management  
- `transport.py` - Network layer

### 🟡 **HIGH PRIORITY**
- `litestar.py` - Modern web framework
- `logging.py` - Core logging integration

### 🟠 **MEDIUM PRIORITY**
- AI module files
- `loguru.py` - Popular logging library
- `arq.py` - Async task queue
- `profiler/utils.py` - Performance monitoring

### 🔵 **LOW PRIORITY**
- Feature flag integrations
- ML/AI specific integrations
- Utility modules
- Script files
- Test files

## Estimated Migration Effort

- **Core SDK Files**: ~150 type annotations to migrate
- **Integration Files**: ~100 type annotations to migrate
- **Script Files**: ~15 type annotations to migrate
- **Test Files**: ~100+ type annotations to migrate

**Total Remaining**: ~365+ comment-based type annotations

## Notes

- Some files contain `# type: ignore` comments which are intentional and should NOT be migrated
- Focus on the CRITICAL and HIGH PRIORITY files first
- Test files can be migrated last as they don't affect production code
- Script files are development-only and lower priority

Last Updated: December 2024